"""SQL contract tests for migration 026 — durable Run Intel sessions.

Proves the migration provides:
  * the ``intel_run_sessions`` table with the required columns + state CHECK;
  * both nullable FKs (``analyst_refresh_jobs.run_session_id``,
    ``intel_v3_snapshots.run_session_id``) referencing the session table;
  * same-day multiple-session capability: the old daily-window unique index
    is dropped and replaced by a session-scoped partial unique index;
  * legacy-null compatibility: the old (user, ticker, window) uniqueness is
    preserved for rows WHERE run_session_id IS NULL;
  * snapshot publication idempotency: at most one snapshot row per session;
  * the service-role/RLS operational-table pattern (deny-all policy);
  * idempotent re-runs (IF NOT EXISTS / additive-only statements).

Also cross-checks the application constants against the SQL so the store's
state machine and the CHECK constraint can never drift apart silently.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.intelligence.v3.intel_run_session_store_v1 import ALL_STATUSES

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "database"
    / "026_intel_run_sessions.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _stripped() -> str:
    """SQL with comment lines removed (so the rollback block never matches)."""
    return "\n".join(
        line for line in _sql().splitlines()
        if not line.strip().startswith("--")
    )


class TestSessionTable:
    def test_migration_file_uses_next_sequential_number(self):
        assert MIGRATION.exists(), "v2/database/026_intel_run_sessions.sql missing"
        siblings = sorted(p.name for p in MIGRATION.parent.glob("0*.sql"))
        assert siblings[-1] == "026_intel_run_sessions.sql"

    def test_creates_session_table_with_required_columns(self):
        sql = _stripped()
        assert "CREATE TABLE IF NOT EXISTS public.intel_run_sessions" in sql
        for col in [
            "id                        UUID PRIMARY KEY",
            "user_id                   UUID NOT NULL",
            "status                    TEXT NOT NULL",
            "holdings_scope            JSONB NOT NULL",
            "stale_tickers             JSONB NOT NULL",
            "expected_ticker_job_count INTEGER NOT NULL",
            "pre_session_snapshot_id   UUID NULL REFERENCES public.intel_v3_snapshots(id)",
            "completed_snapshot_id     UUID NULL REFERENCES public.intel_v3_snapshots(id)",
            "last_error                TEXT",
            "created_at                TIMESTAMPTZ NOT NULL",
            "updated_at                TIMESTAMPTZ NOT NULL",
            "completed_at              TIMESTAMPTZ",
        ]:
            assert col in sql, f"missing column definition: {col!r}"

    def test_status_check_constraint_matches_store_state_machine(self):
        sql = _sql()
        check = re.search(r"CHECK \(status IN \((.*?)\)\)", sql, re.DOTALL)
        assert check, "status CHECK constraint missing"
        sql_statuses = set(re.findall(r"'([a-z_]+)'", check.group(1)))
        assert sql_statuses == set(ALL_STATUSES)
        # The state machine distinguishes every mission-required state.
        assert {
            "created",
            "ticker_refresh_in_progress",
            "publishing",
            "publication_retryable_failed",
            "completed",
            "failed",
        } <= sql_statuses

    def test_service_role_rls_pattern(self):
        sql = _stripped()
        assert "ALTER TABLE public.intel_run_sessions ENABLE ROW LEVEL SECURITY" in sql
        assert "intel_run_sessions_service_only" in sql
        assert re.search(
            r"CREATE POLICY intel_run_sessions_service_only\s+"
            r"ON public\.intel_run_sessions FOR ALL USING \(false\)",
            sql,
        )


class TestJobQueueContract:
    def test_adds_nullable_session_fk_to_jobs(self):
        sql = _stripped()
        assert re.search(
            r"ALTER TABLE public\.analyst_refresh_jobs\s+"
            r"ADD COLUMN IF NOT EXISTS run_session_id UUID NULL\s+"
            r"REFERENCES public\.intel_run_sessions\(id\)",
            sql,
        )

    def test_old_daily_window_uniqueness_is_dropped(self):
        assert (
            "DROP INDEX IF EXISTS uq_analyst_refresh_jobs_user_ticker_window"
            in _stripped()
        )

    def test_session_scoped_uniqueness_allows_same_day_second_session(self):
        sql = _stripped()
        assert re.search(
            r"CREATE UNIQUE INDEX IF NOT EXISTS uq_analyst_refresh_jobs_session_ticker\s+"
            r"ON public\.analyst_refresh_jobs \(run_session_id, ticker\)\s+"
            r"WHERE run_session_id IS NOT NULL",
            sql,
        ), "session jobs must be unique per (run_session_id, ticker) only"
        # Session identity must NOT involve refresh_window.
        session_idx = re.search(
            r"uq_analyst_refresh_jobs_session_ticker.*?;", sql, re.DOTALL,
        ).group(0)
        assert "refresh_window" not in session_idx

    def test_legacy_null_rows_keep_daily_window_uniqueness(self):
        sql = _stripped()
        assert re.search(
            r"CREATE UNIQUE INDEX IF NOT EXISTS uq_analyst_refresh_jobs_legacy_window\s+"
            r"ON public\.analyst_refresh_jobs \(user_id, ticker, refresh_window\)\s+"
            r"WHERE run_session_id IS NULL",
            sql,
        )

    def test_session_claim_count_index_exists(self):
        sql = _stripped()
        assert re.search(
            r"CREATE INDEX IF NOT EXISTS idx_analyst_refresh_jobs_session_status\s+"
            r"ON public\.analyst_refresh_jobs \(run_session_id, status\)",
            sql,
        )


class TestSnapshotContract:
    def test_adds_nullable_session_fk_to_snapshots(self):
        sql = _stripped()
        assert re.search(
            r"ALTER TABLE public\.intel_v3_snapshots\s+"
            r"ADD COLUMN IF NOT EXISTS run_session_id UUID NULL\s+"
            r"REFERENCES public\.intel_run_sessions\(id\)",
            sql,
        )

    def test_publication_idempotency_one_snapshot_per_session(self):
        sql = _stripped()
        assert re.search(
            r"CREATE UNIQUE INDEX IF NOT EXISTS uq_intel_v3_snapshots_run_session\s+"
            r"ON public\.intel_v3_snapshots \(run_session_id\)\s+"
            r"WHERE run_session_id IS NOT NULL",
            sql,
        )

    def test_snapshot_pk_type_matches_fk_type(self):
        """intel_v3_snapshots.id is UUID (migration 016) — the session table's
        snapshot FKs must use the same type, not a guessed one."""
        base = (MIGRATION.parent / "016_intel_v3_snapshots.sql").read_text()
        assert re.search(r"id\s+UUID PRIMARY KEY", base)
        assert "pre_session_snapshot_id   UUID NULL" in _stripped()


class TestIdempotencyAndSafety:
    def test_all_creates_are_guarded(self):
        sql = _stripped()
        for stmt in re.findall(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX)[^;]*;", sql):
            assert "IF NOT EXISTS" in stmt, f"unguarded statement: {stmt[:80]}"
        for stmt in re.findall(r"ALTER TABLE[^;]*ADD COLUMN[^;]*;", sql):
            assert "IF NOT EXISTS" in stmt, f"unguarded statement: {stmt[:80]}"

    def test_no_sentinel_or_fake_ticker_rows(self):
        """The migration stores session state ONLY in the session table —
        no INSERTs anywhere, no sentinel tickers, no control rows."""
        sql = _stripped()
        assert "INSERT INTO" not in sql.upper()
        assert "__run_session_active__" not in _sql()

    def test_no_destructive_statements_beyond_index_swap(self):
        sql = _stripped()
        assert "DROP TABLE" not in sql
        assert "DROP COLUMN" not in sql
        drops = re.findall(r"DROP INDEX IF EXISTS (\w+)", sql)
        assert drops == ["uq_analyst_refresh_jobs_user_ticker_window"]
