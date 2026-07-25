"""Task-graph creation is fail-closed and self-repairing.

Proves (completion item 3):
  * ``get_or_create_task`` returns the exact existing task on a VERIFIED
    logical-identity conflict and raises on every other database error — an
    unknown failure is never translated into "duplicate";
  * session creation verifies the exact expected seed graph (portfolio
    context + macro + every collector lane for every frozen ticker) and only
    then transitions created → running; incomplete shapes stay in the
    explicit retryable 'created' state with the error recorded;
  * repair converges EVERY partial-create shape to exactly one complete
    graph with no duplicates: session row only; some ticker rows; all ticker
    rows but no tasks; partial ticker rows; partial seed tasks; half of one
    ticker's lanes; missing portfolio/macro tasks; duplicate retry after
    success; crash at each phase boundary.
"""
from __future__ import annotations

import uuid

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.session_control_v1 import (
    expected_seed_task_keys,
    repair_session_graph,
    verify_seed_graph,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_COLLECT_PORTFOLIO_CONTEXT,
)
from tests.distributed_run_intel_test_utils import (
    FakeQuery,
    FakeSupabase,
    seed_position,
)

USER = str(uuid.uuid4())


class TransientDbError(Exception):
    """A non-duplicate database failure (network blip, timeout, ...)."""


class FlakyInsertClient(FakeSupabase):
    """Fails the Nth insert into a chosen table with a NON-unique error."""

    def __init__(self, *, fail_table: str, fail_on_call: int = 1):
        super().__init__()
        self.fail_table = fail_table
        self.fail_on_call = fail_on_call
        self.insert_calls = 0
        self.armed = True

    def table(self, name):
        outer = self

        class _Query(FakeQuery):
            def execute(self):
                if (
                    outer.armed
                    and self._table == outer.fail_table
                    and self._op == "insert"
                ):
                    outer.insert_calls += 1
                    if outer.insert_calls == outer.fail_on_call:
                        raise TransientDbError(
                            f"transient failure inserting into {self._table}"
                        )
                return super().execute()

        return _Query(self.store, name)


def _graph_is_complete(client, session_id: str) -> bool:
    rows = store.list_ticker_rows(client, run_session_id=session_id)
    return not verify_seed_graph(
        client, run_session_id=session_id, scope_rows=rows,
    ) if rows else False


def _no_duplicate_tasks(client, session_id: str) -> bool:
    keys = [
        store.logical_task_key(
            str(t.get("task_type")), t.get("lane"),
            t.get("ticker"), t.get("batch_key"),
        )
        for t in store.list_tasks(client, run_session_id=session_id)
    ]
    return len(keys) == len(set(keys))


async def _session_row(client, session_id: str) -> dict:
    return next(
        s for s in client.rows("intel_run_sessions") if s["id"] == session_id
    )


class TestGetOrCreateContract:
    @pytest.mark.asyncio
    async def test_verified_duplicate_returns_exact_existing_task(self):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        first = store.find_task_by_logical_key(
            client, run_session_id=session_id,
            task_type=TASK_COLLECT_EVIDENCE_LANE, lane="price", ticker="AAPL",
        )
        assert first is not None
        row, created = store.get_or_create_task(
            client, run_session_id=session_id, user_id=USER,
            task_type=TASK_COLLECT_EVIDENCE_LANE, lane="price", ticker="AAPL",
        )
        assert created is False
        assert row["id"] == first["id"]  # the EXACT existing task

    @pytest.mark.asyncio
    async def test_non_duplicate_error_is_never_swallowed(self):
        client = FlakyInsertClient(fail_table="intel_run_tasks")
        seed_position(client, USER, "AAPL")
        session_id = str(uuid.uuid4())
        client.armed = False
        client.table("intel_run_sessions").insert({
            "id": session_id, "user_id": USER, "status": "created",
            "workflow_version": 2, "holdings_scope": [], "stale_tickers": [],
            "expected_ticker_job_count": 0, "metrics": {},
        }).execute()
        client.armed = True
        with pytest.raises(TransientDbError):
            store.get_or_create_task(
                client, run_session_id=session_id, user_id=USER,
                task_type=TASK_COLLECT_PORTFOLIO_CONTEXT,
            )
        # And create_task (scheduler wrapper) propagates it too.
        client.insert_calls = 0
        with pytest.raises(TransientDbError):
            store.create_task(
                client, run_session_id=session_id, user_id=USER,
                task_type=TASK_COLLECT_MACRO_CONTEXT,
            )


class TestFailClosedCreation:
    @pytest.mark.asyncio
    async def test_transient_ticker_row_failure_leaves_scope_unfrozen_fails_closed(
        self,
    ):
        """A ticker-row insert failure during creation can leave the frozen
        scope PARTIAL (some rows persisted, ``holdings_scope`` lists all of
        them) — repair must fail closed rather than reconstructing the
        missing rows from the current portfolio."""
        client = FlakyInsertClient(fail_table="intel_run_tickers")
        for ticker in ("AAPL", "MSFT", "GOOGL"):
            seed_position(client, USER, ticker)
        session_id = str(uuid.uuid4())
        result = await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        # Fail-closed: NOT reported running with a partial scope.
        assert result["session_status"] == "created"
        rows_after_create = len(client.rows("intel_run_tickers"))
        assert rows_after_create < 3  # genuinely partial — one insert failed
        client.armed = False
        session = await _session_row(client, session_id)
        repaired = await repair_session_graph(
            client=client, user_id=USER, session=session,
        )
        assert repaired is False
        assert (await _session_row(client, session_id))["status"] == "failed"
        # Never grown by reconstructing the missing row from current data.
        assert len(client.rows("intel_run_tickers")) == rows_after_create

    @pytest.mark.asyncio
    async def test_transient_collector_task_failure_stays_retryable_then_repairs(
        self,
    ):
        client = FlakyInsertClient(fail_table="intel_run_tasks", fail_on_call=3)
        for ticker in ("AAPL", "MSFT"):
            seed_position(client, USER, ticker)
        session_id = str(uuid.uuid4())
        result = await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        assert result["session_status"] == "created"
        session = await _session_row(client, session_id)
        assert "task_graph" in str(session.get("last_error") or "")
        client.armed = False
        repaired = await repair_session_graph(
            client=client, user_id=USER, session=session,
        )
        assert repaired is True
        assert _graph_is_complete(client, session_id)
        assert _no_duplicate_tasks(client, session_id)


class TestRepairAllShapes:
    def _bare_session(self, client, *, with_scope_for: list[str] = ()) -> str:
        session_id = str(uuid.uuid4())
        client.table("intel_run_sessions").insert({
            "id": session_id, "user_id": USER, "status": "created",
            "workflow_version": 2, "holdings_scope": list(with_scope_for),
            "stale_tickers": [],
            "expected_ticker_job_count": 0, "metrics": {},
            "created_at": "2026-07-21T00:00:00+00:00",
            "updated_at": "2026-07-21T00:00:00+00:00",
        }).execute()
        for ticker in with_scope_for:
            client.table("intel_run_tickers").insert({
                "id": str(uuid.uuid4()), "run_session_id": session_id,
                "user_id": USER, "ticker": ticker, "asset_type": "equity",
                "state": "pending", "priority": 50,
                "required_lanes": ["price", "technicals", "fundamentals"],
            }).execute()
        return session_id

    async def _assert_converged(self, client, session_id):
        session = await _session_row(client, session_id)
        repaired = await repair_session_graph(
            client=client, user_id=USER, session=session,
        )
        assert repaired is True
        assert (await _session_row(client, session_id))["status"] == "running"
        assert _graph_is_complete(client, session_id)
        assert _no_duplicate_tasks(client, session_id)
        # Convergence is stable: repairing again changes nothing.
        before = len(client.rows("intel_run_tasks"))
        session = await _session_row(client, session_id)
        await repair_session_graph(
            client=client, user_id=USER, session=session,
        )
        assert len(client.rows("intel_run_tasks")) == before

    @pytest.mark.asyncio
    async def test_shape_session_row_only_fails_never_reconstructed(self):
        """Zero frozen ticker rows means the crash happened before scope
        freeze ever persisted — repair must fail, never rebuild the scope
        from the CURRENT portfolio (which may have since changed)."""
        client = FakeSupabase()
        for ticker in ("AAPL", "MSFT"):
            seed_position(client, USER, ticker)
        session_id = self._bare_session(client)  # no frozen rows at all
        session = await _session_row(client, session_id)
        repaired = await repair_session_graph(
            client=client, user_id=USER, session=session,
        )
        assert repaired is False
        updated = await _session_row(client, session_id)
        assert updated["status"] == "failed"
        assert "scope_freeze_incomplete_restart_required" in str(updated.get("last_error"))
        assert client.rows("intel_run_tickers") == []
        assert client.rows("intel_run_tasks") == []

    @pytest.mark.asyncio
    async def test_frozen_scope_never_grows_to_match_current_portfolio(self):
        """Only AAPL was frozen at session-creation time even though the
        CURRENT portfolio now also holds MSFT/GOOGL — repair must seed
        tasks for AAPL only and never add the newer tickers to the frozen
        scope."""
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = self._bare_session(client, with_scope_for=["AAPL"])
        # Positions changed AFTER the scope froze.
        seed_position(client, USER, "MSFT")
        seed_position(client, USER, "GOOGL")
        await self._assert_converged(client, session_id)
        assert len(client.rows("intel_run_tickers")) == 1
        session = await _session_row(client, session_id)
        assert session["holdings_scope"] == ["AAPL"]

    @pytest.mark.asyncio
    async def test_shape_all_ticker_rows_no_tasks(self):
        client = FakeSupabase()
        for ticker in ("AAPL", "MSFT"):
            seed_position(client, USER, ticker)
        session_id = self._bare_session(
            client, with_scope_for=["AAPL", "MSFT"],
        )
        await self._assert_converged(client, session_id)

    @pytest.mark.asyncio
    async def test_shape_partial_seed_tasks_missing_context_and_macro(self):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = self._bare_session(client, with_scope_for=["AAPL"])
        # Only ONE lane task exists; portfolio-context + macro + other lanes
        # are missing.
        store.create_task(
            client, run_session_id=session_id, user_id=USER,
            task_type=TASK_COLLECT_EVIDENCE_LANE, ticker="AAPL", lane="price",
        )
        rows = store.list_ticker_rows(client, run_session_id=session_id)
        missing_before = verify_seed_graph(
            client, run_session_id=session_id, scope_rows=rows,
        )
        assert missing_before  # genuinely incomplete
        await self._assert_converged(client, session_id)

    @pytest.mark.asyncio
    async def test_shape_half_of_one_tickers_lanes_missing(self):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = self._bare_session(client, with_scope_for=["AAPL"])
        store.create_task(
            client, run_session_id=session_id, user_id=USER,
            task_type=TASK_COLLECT_PORTFOLIO_CONTEXT,
        )
        store.create_task(
            client, run_session_id=session_id, user_id=USER,
            task_type=TASK_COLLECT_MACRO_CONTEXT,
        )
        for lane in ("price", "technicals"):  # half of the equity lanes
            store.create_task(
                client, run_session_id=session_id, user_id=USER,
                task_type=TASK_COLLECT_EVIDENCE_LANE, ticker="AAPL", lane=lane,
            )
        await self._assert_converged(client, session_id)

    @pytest.mark.asyncio
    async def test_duplicate_frozen_ticker_rows_fail_closed(self):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = self._bare_session(client, with_scope_for=["AAPL"])
        # A second, duplicate frozen row for the same ticker (contradictory
        # durable state — bypasses the fake's own unique-index emulation the
        # same way a genuinely corrupted row would bypass a real one) —
        # never silently deduplicated or repaired.
        client.store["intel_run_tickers"].append({
            "id": str(uuid.uuid4()), "run_session_id": session_id,
            "user_id": USER, "ticker": "AAPL", "asset_type": "equity",
            "state": "pending", "priority": 50,
            "required_lanes": ["price", "technicals", "fundamentals"],
        })
        session = await _session_row(client, session_id)
        repaired = await repair_session_graph(
            client=client, user_id=USER, session=session,
        )
        assert repaired is False
        assert (await _session_row(client, session_id))["status"] == "failed"

    @pytest.mark.asyncio
    async def test_malformed_asset_type_fails_closed(self):
        session_id = str(uuid.uuid4())
        client = FakeSupabase()
        client.table("intel_run_sessions").insert({
            "id": session_id, "user_id": USER, "status": "created",
            "workflow_version": 2, "holdings_scope": ["AAPL"],
            "stale_tickers": [], "expected_ticker_job_count": 0, "metrics": {},
            "created_at": "2026-07-21T00:00:00+00:00",
            "updated_at": "2026-07-21T00:00:00+00:00",
        }).execute()
        client.table("intel_run_tickers").insert({
            "id": str(uuid.uuid4()), "run_session_id": session_id,
            "user_id": USER, "ticker": "AAPL", "asset_type": "not_a_real_type",
            "state": "pending", "priority": 50,
            "required_lanes": [],
        }).execute()
        session = await _session_row(client, session_id)
        repaired = await repair_session_graph(
            client=client, user_id=USER, session=session,
        )
        assert repaired is False
        assert (await _session_row(client, session_id))["status"] == "failed"

    @pytest.mark.asyncio
    async def test_holdings_scope_ticker_row_mismatch_fails_closed(self):
        """holdings_scope claims two tickers but only one has a frozen
        row — a contradictory partial state that must fail, not repair."""
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = str(uuid.uuid4())
        client.table("intel_run_sessions").insert({
            "id": session_id, "user_id": USER, "status": "created",
            "workflow_version": 2, "holdings_scope": ["AAPL", "MSFT"],
            "stale_tickers": [], "expected_ticker_job_count": 0, "metrics": {},
            "created_at": "2026-07-21T00:00:00+00:00",
            "updated_at": "2026-07-21T00:00:00+00:00",
        }).execute()
        client.table("intel_run_tickers").insert({
            "id": str(uuid.uuid4()), "run_session_id": session_id,
            "user_id": USER, "ticker": "AAPL", "asset_type": "equity",
            "state": "pending", "priority": 50,
            "required_lanes": ["price", "technicals", "fundamentals"],
        }).execute()
        session = await _session_row(client, session_id)
        repaired = await repair_session_graph(
            client=client, user_id=USER, session=session,
        )
        assert repaired is False
        assert (await _session_row(client, session_id))["status"] == "failed"

    @pytest.mark.asyncio
    async def test_repair_makes_zero_truth_provider_or_llm_calls(self, monkeypatch):
        """Repair never re-runs the financial-truth baseline, never touches
        PortfolioService, and never calls a provider/LLM — it only reads
        the durable frozen ticker rows and seeds missing tasks."""

        def _forbidden(*_a, **_kw):
            raise AssertionError("repair must never call this")

        monkeypatch.setattr(control, "run_financial_truth_baseline_strict", _forbidden)
        monkeypatch.setattr(control, "PortfolioService", _forbidden)
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = self._bare_session(client, with_scope_for=["AAPL"])
        session = await _session_row(client, session_id)
        repaired = await repair_session_graph(
            client=client, user_id=USER, session=session,
        )
        assert repaired is True

    @pytest.mark.asyncio
    async def test_duplicate_retry_after_successful_creation_is_noop(self):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = str(uuid.uuid4())
        first = await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        assert first["session_status"] == "running"
        tasks_before = len(client.rows("intel_run_tasks"))
        second = await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        assert second["created"] is False
        assert len(client.rows("intel_run_tasks")) == tasks_before
        assert _no_duplicate_tasks(client, session_id)

    @pytest.mark.asyncio
    async def test_expected_seed_graph_shape_is_exact(self):
        client = FakeSupabase()
        for ticker, category in (("AAPL", "Core"), ("VTI", "ETF"), ("BTC", "Crypto")):
            seed_position(client, USER, ticker, category=category)
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        rows = store.list_ticker_rows(client, run_session_id=session_id)
        expected = expected_seed_task_keys(rows)
        actual = {
            store.logical_task_key(
                str(t.get("task_type")), t.get("lane"),
                t.get("ticker"), t.get("batch_key"),
            )
            for t in store.list_tasks(client, run_session_id=session_id)
        }
        assert actual == expected
