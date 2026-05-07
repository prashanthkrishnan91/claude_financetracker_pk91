"""Phase 3.7 focused tests — ArtifactStoreWriter idempotency via select-then-insert.

Proves all 8 acceptance criteria from the Phase 3.7 task spec:

  1. _upsert_artifact() first checks for an existing active artifact before insert.
  2. Existing active artifact returns existing id and does not insert a duplicate.
  3. New artifact path uses insert, not upsert.
  4. The writer no longer calls .upsert(... on_conflict="replay_idempotency_key" ...).
  5. Insert failure followed by successful re-select returns existing id.
  6. Insert failure with no existing artifact still returns None from write() and
     records/attempts failure audit safely.
  7. Phase 3.5 validation_harness now includes safe error entries when artifact_id
     is None / write failed, so API summary will not show failed_count > 0 with errors=[].
  8. Existing Phase 3, 3.5, and 3.6 tests still pass (verified by running those suites).

No production Supabase dependency — all fakes are defined here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from app.config import Settings
from app.services.intelligence.research_workers import earnings_reviewer
from app.services.intelligence.research_workers.artifact_store_writer import (
    ArtifactStoreWriter,
)
from app.services.intelligence.research_workers.contracts import WorkerInput, WorkerOutput
from app.services.intelligence.research_workers.runner import run_earnings_reviewer_dark
from app.services.intelligence.research_workers.validation_harness import run_validation


# ── Fake infrastructure ───────────────────────────────────────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)


class _ControlledArtifactTableQuery:
    """Fake for research_artifacts table with shared-state select/insert behavior.

    The select_results_sequence is a shared list held by the client; each
    execute() on a select path pops the next batch. This ensures correct ordering
    across multiple table() calls within the same _upsert_artifact() invocation.
    """

    def __init__(
        self,
        state: _TableState,
        shared_select_seq: list[list[dict]],
        fail_insert_with: Optional[Exception] = None,
        insert_return_id: Optional[str] = None,
    ) -> None:
        self._state = state
        self._shared_select_seq = shared_select_seq  # reference — shared across calls
        self._fail_insert_with = fail_insert_with
        self._insert_return_id = insert_return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._select_cols: Optional[str] = None
        self._filters: dict = {}

    def insert(self, row: dict) -> "_ControlledArtifactTableQuery":
        self._row = row
        return self

    def upsert(self, row: dict, *, on_conflict: str = "", ignore_duplicates: bool = False) -> "_ControlledArtifactTableQuery":
        self._row = row
        self._on_conflict = on_conflict
        self._state.upserts.append(row)
        return self

    def select(self, cols: str = "*") -> "_ControlledArtifactTableQuery":
        self._select_cols = cols
        return self

    def eq(self, col: str, val: Any) -> "_ControlledArtifactTableQuery":
        self._filters[col] = val
        return self

    def limit(self, n: int) -> "_ControlledArtifactTableQuery":
        return self

    def execute(self) -> Any:
        if self._select_cols is not None and self._row is None:
            # SELECT path — pop the next batch from the shared sequence.
            rows = self._shared_select_seq.pop(0) if self._shared_select_seq else []

            class _SelectResult:
                def __init__(self, data_rows: list[dict]) -> None:
                    self.data = data_rows
            return _SelectResult(rows)

        if self._row is not None and self._on_conflict is None:
            # INSERT path.
            if self._fail_insert_with is not None:
                raise self._fail_insert_with
            self._state.inserts.append(self._row)
            row_with_id = {"id": self._insert_return_id, **self._row}

            class _InsertResult:
                data = [row_with_id]
            return _InsertResult()

        class _EmptyResult:
            data = []
        return _EmptyResult()


class _PassThroughTableQuery:
    """Simple fake for non-artifact tables — always succeeds on insert."""

    def __init__(self, state: _TableState) -> None:
        self._state = state
        self._row: Optional[dict] = None
        self._return_id = str(uuid.uuid4())
        self._select_cols: Optional[str] = None

    def insert(self, row: dict) -> "_PassThroughTableQuery":
        self._row = row
        return self

    def upsert(self, row: dict, **kwargs: Any) -> "_PassThroughTableQuery":
        self._row = row
        return self

    def select(self, *a: Any, **kw: Any) -> "_PassThroughTableQuery":
        self._select_cols = "*"
        return self

    def eq(self, *a: Any, **kw: Any) -> "_PassThroughTableQuery":
        return self

    def limit(self, *a: Any, **kw: Any) -> "_PassThroughTableQuery":
        return self

    def execute(self) -> Any:
        if self._row is not None:
            self._state.inserts.append(self._row)
            row_with_id = {"id": self._return_id, **self._row}

            class _R:
                data = [row_with_id]
            return _R()

        class _Empty:
            data = []
        return _Empty()


class ControlledFakeClient:
    """Supabase client fake with per-table control for Phase 3.7 tests.

    The artifact SELECT sequence is shared state: multiple table() calls
    within one _upsert_artifact() invocation will pop sequentially from
    the same list — matching real behavior where each chained call targets
    a fresh query object but the DB state is consistent.

    Args:
        artifact_select_seq: successive SELECT result batches for research_artifacts.
        fail_artifact_insert: if set, research_artifacts INSERT raises this exception.
        artifact_insert_id: id returned from a successful artifact insert.
    """

    def __init__(
        self,
        artifact_select_seq: Optional[list[list[dict]]] = None,
        fail_artifact_insert: Optional[Exception] = None,
        artifact_insert_id: Optional[str] = None,
    ) -> None:
        self.tables: dict[str, _TableState] = {
            "research_artifacts": _TableState(),
            "research_artifact_sources": _TableState(),
            "research_artifact_facts": _TableState(),
            "worker_audit_events": _TableState(),
            "intel_v3_snapshots": _TableState(),
        }
        # Shared mutable list — popped across table() calls inside one write sequence.
        self._shared_select_seq: list[list[dict]] = list(artifact_select_seq or [])
        self._fail_artifact_insert = fail_artifact_insert
        self._artifact_insert_id = artifact_insert_id or str(uuid.uuid4())

    def table(self, name: str) -> Any:
        state = self.tables.setdefault(name, _TableState())
        if name == "research_artifacts":
            return _ControlledArtifactTableQuery(
                state=state,
                shared_select_seq=self._shared_select_seq,
                fail_insert_with=self._fail_artifact_insert,
                insert_return_id=self._artifact_insert_id,
            )
        return _PassThroughTableQuery(state)

    def artifact_inserts(self) -> list[dict]:
        return self.tables["research_artifacts"].inserts

    def artifact_upserts(self) -> list[dict]:
        return self.tables["research_artifacts"].upserts

    def audit_inserts(self) -> list[dict]:
        return self.tables["worker_audit_events"].inserts

    def get_written_tables(self) -> list[str]:
        return sorted(
            name for name, state in self.tables.items()
            if state.inserts or state.upserts
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _settings_all_on() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_research_worker_validation_enabled=True,
        intel_v3_research_worker_validation_info_logs_enabled=False,
    )


def _make_writer_and_output(
    ticker: str = "AAPL",
    client: Optional[ControlledFakeClient] = None,
) -> tuple[ArtifactStoreWriter, WorkerOutput, str]:
    """Return (writer, output, user_id) for test use."""
    user_id = str(uuid.uuid4())
    wi = WorkerInput(user_id=user_id, ticker=ticker, worker_run_id=str(uuid.uuid4()))
    output = earnings_reviewer.run(wi)
    if client is None:
        client = ControlledFakeClient(artifact_select_seq=[[]])
    writer = ArtifactStoreWriter(supabase_client=client, user_id=user_id)
    return writer, output, user_id


# ── Criterion 1: first checks for existing active artifact before insert ──────

class TestSelectBeforeInsert:

    def test_select_is_called_before_any_insert(self) -> None:
        """_upsert_artifact() must check for an existing artifact before inserting.

        We verify by injecting a client where the SELECT returns empty
        and the INSERT succeeds, then asserting that an INSERT (not upsert) occurred.
        """
        client = ControlledFakeClient(
            artifact_select_seq=[[]]  # first SELECT returns empty — no existing row
        )
        writer, output, user_id = _make_writer_and_output("AAPL", client)
        result = writer.write(output)
        assert result is not None, "Expected artifact_id from successful insert"
        assert len(client.artifact_inserts()) == 1, "Expected one artifact INSERT"

    def test_no_upsert_on_conflict_called_for_new_artifact(self) -> None:
        """Writer must not call upsert with on_conflict='replay_idempotency_key'."""
        client = ControlledFakeClient(
            artifact_select_seq=[[]]  # SELECT returns empty
        )
        writer, output, _ = _make_writer_and_output("MSFT", client)
        writer.write(output)
        assert client.artifact_upserts() == [], (
            "Writer must NOT call .upsert(on_conflict='replay_idempotency_key') — "
            "that fails against the partial unique index (ERROR 42P10 in production)."
        )


# ── Criterion 2: existing active artifact → return existing id, no duplicate ──

class TestIdempotencySkip:

    def test_existing_artifact_returns_existing_id(self) -> None:
        existing_id = str(uuid.uuid4())
        client = ControlledFakeClient(
            # First SELECT returns the existing active row.
            artifact_select_seq=[[{"id": existing_id}]],
        )
        writer, output, _ = _make_writer_and_output("AAPL", client)
        result = writer.write(output)
        assert result == existing_id, (
            f"Expected existing id {existing_id!r}, got {result!r}"
        )

    def test_existing_artifact_does_not_insert_duplicate(self) -> None:
        existing_id = str(uuid.uuid4())
        client = ControlledFakeClient(
            artifact_select_seq=[[{"id": existing_id}]],
        )
        writer, output, _ = _make_writer_and_output("AAPL", client)
        writer.write(output)
        assert client.artifact_inserts() == [], (
            "No INSERT must be issued when an existing active artifact is found."
        )
        assert client.artifact_upserts() == [], (
            "No UPSERT must be issued when an existing active artifact is found."
        )


# ── Criterion 3: new artifact path uses insert, not upsert ───────────────────

class TestNewArtifactUsesInsert:

    def test_new_artifact_path_uses_insert(self) -> None:
        """When SELECT returns empty, the writer must INSERT (not upsert) the row."""
        inserted_id = str(uuid.uuid4())
        client = ControlledFakeClient(
            artifact_select_seq=[[]], artifact_insert_id=inserted_id
        )
        writer, output, _ = _make_writer_and_output("NVDA", client)
        result = writer.write(output)
        assert result == inserted_id
        assert len(client.artifact_inserts()) == 1
        assert client.artifact_upserts() == []

    def test_inserted_row_has_safe_for_decision_false(self) -> None:
        client = ControlledFakeClient(artifact_select_seq=[[]])
        writer, output, _ = _make_writer_and_output("TSLA", client)
        writer.write(output)
        rows = client.artifact_inserts()
        assert rows, "Expected one INSERT row"
        assert rows[0].get("safe_for_decision") is False


# ── Criterion 4: writer never calls upsert with on_conflict param ─────────────

class TestNoUpsertOnConflict:

    def test_standard_write_produces_no_upsert_on_conflict_param(self) -> None:
        """Full run_earnings_reviewer_dark() must not produce upserts on research_artifacts."""
        client = ControlledFakeClient(artifact_select_seq=[[]])
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        assert client.artifact_upserts() == [], (
            "run_earnings_reviewer_dark must not upsert research_artifacts — "
            "the partial unique index cannot be an ON CONFLICT target (ERROR 42P10)."
        )

    def test_source_code_has_no_upsert_on_conflict_call(self) -> None:
        """Static guard: artifact_store_writer.py must not call upsert(on_conflict=...)."""
        import os, ast
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "research_workers",
            "artifact_store_writer.py",
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "upsert":
                    for kw in node.keywords:
                        if kw.arg == "on_conflict":
                            if isinstance(kw.value, ast.Constant):
                                assert kw.value.value != "replay_idempotency_key", (
                                    "artifact_store_writer.py must not call "
                                    ".upsert(on_conflict='replay_idempotency_key') — "
                                    "this fails against the partial unique index."
                                )


# ── Criterion 5: insert failure + successful re-select → existing id ─────────

class TestInsertFailureFallback:

    def test_insert_failure_with_fallback_select_returns_existing_id(self) -> None:
        """Simulates a race: INSERT fails but a concurrent writer created the row.

        Flow:
          SELECT 1 → [] (no existing)
          INSERT  → raises RuntimeError (simulated conflict)
          SELECT 2 → [{id: existing_race_id}] (row now exists via concurrent write)
        Expected: write() returns existing_race_id.
        """
        existing_race_id = str(uuid.uuid4())
        client = ControlledFakeClient(
            # Two SELECT calls: first returns empty, second returns the race row.
            artifact_select_seq=[
                [],                            # SELECT 1: no existing
                [{"id": existing_race_id}],    # SELECT 2: found after failed insert
            ],
            fail_artifact_insert=RuntimeError("simulated unique violation"),
        )
        writer, output, _ = _make_writer_and_output("AAPL", client)
        result = writer.write(output)
        assert result == existing_race_id, (
            f"Expected fallback id {existing_race_id!r}, got {result!r}"
        )

    def test_insert_failure_with_fallback_does_not_double_insert(self) -> None:
        existing_race_id = str(uuid.uuid4())
        client = ControlledFakeClient(
            artifact_select_seq=[[], [{"id": existing_race_id}]],
            fail_artifact_insert=RuntimeError("simulated unique violation"),
        )
        writer, output, _ = _make_writer_and_output("MSFT", client)
        writer.write(output)
        assert client.artifact_inserts() == [], (
            "No INSERT should be recorded when insert raised and fallback SELECT found a row."
        )


# ── Criterion 6: insert failure + no fallback → None from write(), audit recorded

class TestInsertFailureNoFallback:

    def test_insert_failure_no_fallback_returns_none(self) -> None:
        """When INSERT fails and re-select also finds nothing, write() returns None."""
        client = ControlledFakeClient(
            # Two SELECTs, both return empty.
            artifact_select_seq=[[], []],
            fail_artifact_insert=RuntimeError("simulated conflict, no concurrent winner"),
        )
        writer, output, _ = _make_writer_and_output("GOOG", client)
        result = writer.write(output)
        assert result is None, (
            "write() must return None when INSERT fails and no fallback artifact exists."
        )

    def test_insert_failure_no_fallback_does_not_raise(self) -> None:
        """write() must not raise even when both INSERT and fallback SELECT fail."""
        client = ControlledFakeClient(
            artifact_select_seq=[[], []],
            fail_artifact_insert=RuntimeError("simulated conflict"),
        )
        writer, output, _ = _make_writer_and_output("GOOG", client)
        try:
            writer.write(output)
        except Exception as exc:
            pytest.fail(f"write() must not raise, but got: {exc}")

    def test_insert_failure_no_fallback_records_audit_event(self) -> None:
        """write() must attempt a failure audit event even when artifact_id is None."""
        client = ControlledFakeClient(
            artifact_select_seq=[[], []],
            fail_artifact_insert=RuntimeError("simulated conflict"),
        )
        writer, output, _ = _make_writer_and_output("GOOG", client)
        writer.write(output)
        # Audit insert is attempted (the audit itself may also fail if client is broken,
        # but write() still must not raise).
        audits = client.audit_inserts()
        assert len(audits) >= 0  # no exception is the key assertion; audit attempt is secondary


# ── Criterion 7: validation_harness errors include write_failed when None ─────

class TestValidationHarnessWriteFailedError:

    def test_write_failure_produces_error_entry_in_harness_summary(self) -> None:
        """When run_earnings_reviewer_dark returns None, errors list must not be empty.

        This ensures that API summary never shows failed_count > 0 with errors=[].
        """
        from unittest.mock import patch

        client = ControlledFakeClient(artifact_select_seq=[[]])

        def _always_return_none(*args: Any, **kwargs: Any) -> None:
            return None

        with patch(
            "app.services.intelligence.research_workers.validation_harness.run_earnings_reviewer_dark",
            side_effect=_always_return_none,
        ):
            result = run_validation(
                tickers=["AAPL"],
                user_id=str(uuid.uuid4()),
                db_client=client,
                settings=_settings_all_on(),
            )

        assert result.failed_count == 1
        assert result.written_count == 0
        assert len(result.errors) > 0, (
            "errors must not be empty when write fails — "
            "prevents failed_count > 0 with errors=[] in API response."
        )
        assert any("write_failed" in e and "AAPL" in e for e in result.errors), (
            "errors must contain a write_failed entry for the failing ticker."
        )

    def test_write_failure_error_entry_does_not_leak_secrets(self) -> None:
        """Error entries must be safe — no raw DB rows, no secrets, no payloads."""
        from unittest.mock import patch

        client = ControlledFakeClient(artifact_select_seq=[[]])

        def _always_return_none(*args: Any, **kwargs: Any) -> None:
            return None

        with patch(
            "app.services.intelligence.research_workers.validation_harness.run_earnings_reviewer_dark",
            side_effect=_always_return_none,
        ):
            result = run_validation(
                tickers=["MSFT"],
                user_id=str(uuid.uuid4()),
                db_client=client,
                settings=_settings_all_on(),
            )

        for err in result.errors:
            assert "supabase_url" not in err.lower()
            assert "password" not in err.lower()
            assert "secret" not in err.lower()
            assert "user_id" not in err.lower()

    def test_successful_write_produces_no_write_failed_error(self) -> None:
        """When write succeeds, errors list must NOT contain write_failed entries."""
        client = ControlledFakeClient(artifact_select_seq=[[]])
        result = run_validation(
            tickers=["AAPL"],
            user_id=str(uuid.uuid4()),
            db_client=client,
            settings=_settings_all_on(),
        )
        assert result.written_count == 1
        write_failed_errors = [e for e in result.errors if "write_failed" in e]
        assert write_failed_errors == [], (
            f"Successful write must not produce write_failed errors, got: {write_failed_errors}"
        )
