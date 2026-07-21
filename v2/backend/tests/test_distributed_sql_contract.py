"""Migration 027 SQL contract — distributed Run Intel task graph schema.

File-text contract tests (same convention as
test_run_intel_session_sql_contract.py): the migration is read directly and
asserted with exact-string/regex checks, cross-checked against the
application constants so SQL and code cannot drift.

Also verifies the retention cleanup script covers every new table with
FK-safe, active-session-preserving deletes.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    ALL_TASK_STATES,
    ALL_TICKER_STATES,
    SESSION_COMPLETED_WITH_GAPS,
    SESSION_RUNNING,
    SESSION_SUPERSEDED,
)

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "database" / "027_intel_run_distributed_tasks.sql"
)
RETENTION = (
    Path(__file__).resolve().parents[2]
    / "database" / "cost_guard_retention_cleanup.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _stripped() -> str:
    """Drop comment-only lines so ROLLBACK comments never match assertions."""
    return "\n".join(
        line for line in _sql().splitlines()
        if not line.lstrip().startswith("--")
    )


class TestSessionExtensions:
    def test_workflow_version_stage_metrics_columns(self):
        sql = _stripped()
        assert "ADD COLUMN IF NOT EXISTS workflow_version" in sql
        assert "ADD COLUMN IF NOT EXISTS current_stage" in sql
        assert "ADD COLUMN IF NOT EXISTS metrics" in sql

    def test_status_check_extended_with_new_states(self):
        sql = _stripped()
        for state in (SESSION_RUNNING, SESSION_COMPLETED_WITH_GAPS,
                      SESSION_SUPERSEDED):
            assert f"'{state}'" in sql

    def test_one_active_session_per_user_partial_unique_index(self):
        sql = _stripped()
        assert "uq_intel_run_sessions_active_per_user" in sql
        assert re.search(
            r"uq_intel_run_sessions_active_per_user\s*\n?\s*ON public\.intel_run_sessions \(user_id\)",
            sql,
        )
        assert "workflow_version >= 2" in sql

    def test_legacy_supersession_update(self):
        sql = _stripped()
        match = re.search(
            r"UPDATE public\.intel_run_sessions\s*\nSET status = 'superseded'(.*?);",
            sql, re.S,
        )
        assert match, "legacy supersession UPDATE missing"
        block = match.group(0)
        assert "workflow_version = 1" in block
        # Only unfinished states are superseded — terminal history untouched,
        # never rewritten as successful.
        for state in ("'created'", "'ticker_refresh_in_progress'",
                      "'publishing'", "'publication_retryable_failed'"):
            assert state in block
        assert "'completed'" not in block


class TestTickerTable:
    def test_table_and_columns(self):
        sql = _stripped()
        assert "CREATE TABLE IF NOT EXISTS public.intel_run_tickers" in sql
        for column in (
            "run_session_id", "user_id", "ticker", "asset_type", "quantity",
            "market_value", "portfolio_weight_pct", "cost_basis",
            "unrealized_gain_pct", "tax_summary", "prior_action", "priority",
            "required_lanes", "state", "missing_lanes", "degraded_lanes",
            "degradation_reasons", "evidence_bundle", "decision",
        ):
            assert re.search(rf"^\s+{column}\s", sql, re.M), (
                f"intel_run_tickers missing column {column}"
            )

    def test_state_check_matches_code_constants(self):
        sql = _sql()
        table = sql[
            sql.index("CREATE TABLE IF NOT EXISTS public.intel_run_tickers"):
            sql.index("CREATE TABLE IF NOT EXISTS public.intel_run_tasks")
        ]
        for state in ALL_TICKER_STATES:
            assert f"'{state}'" in table, f"ticker state {state} missing from SQL"

    def test_session_ticker_unique_and_cascade(self):
        sql = _stripped()
        assert "uq_intel_run_tickers_session_ticker" in sql
        assert re.search(
            r"intel_run_tickers[\s\S]{0,600}REFERENCES public\.intel_run_sessions\(id\)\s*\n?\s*ON DELETE CASCADE",
            sql,
        )

    def test_rls_deny_all(self):
        sql = _stripped()
        assert "ALTER TABLE public.intel_run_tickers ENABLE ROW LEVEL SECURITY" in sql
        assert "intel_run_tickers_service_only" in sql
        assert re.search(
            r"intel_run_tickers_service_only\s*\n?\s*ON public\.intel_run_tickers FOR ALL USING \(false\)",
            sql,
        )

    def test_cross_user_owner_guard_trigger(self):
        sql = _stripped()
        assert "intel_run_ticker_owner_guard" in sql
        assert "trg_intel_run_tickers_owner_guard" in sql


class TestTaskTable:
    def test_table_and_columns(self):
        sql = _stripped()
        assert "CREATE TABLE IF NOT EXISTS public.intel_run_tasks" in sql
        for column in (
            "run_session_id", "user_id", "ticker", "batch_key", "task_type",
            "lane", "asset_type", "state", "priority", "attempts",
            "max_attempts", "claim_owner", "claimed_at", "lease_expires_at",
            "next_retry_at", "input_fingerprint", "output_ref", "output",
            "error_code", "error_detail", "started_at", "completed_at",
        ):
            assert re.search(rf"^\s+{column}\s", sql, re.M), (
                f"intel_run_tasks missing column {column}"
            )

    def test_state_check_matches_code_constants(self):
        sql = _sql()
        table = sql[
            sql.index("CREATE TABLE IF NOT EXISTS public.intel_run_tasks"):
            sql.index("CREATE OR REPLACE FUNCTION public.claim_intel_run_tasks")
        ]
        for state in ALL_TASK_STATES:
            assert f"'{state}'" in table, f"task state {state} missing from SQL"

    def test_logical_idempotency_unique_index_normalizes_nulls(self):
        sql = _stripped()
        assert "uq_intel_run_tasks_logical" in sql
        block = sql[sql.index("uq_intel_run_tasks_logical"):]
        for expr in ("COALESCE(lane, '')", "COALESCE(ticker, '')",
                     "COALESCE(batch_key, '')"):
            assert expr in block[:600]

    def test_rls_and_owner_guard(self):
        sql = _stripped()
        assert "ALTER TABLE public.intel_run_tasks ENABLE ROW LEVEL SECURITY" in sql
        assert "intel_run_tasks_service_only" in sql
        assert "trg_intel_run_tasks_owner_guard" in sql


class TestClaimRpc:
    def test_claim_function_uses_skip_locked(self):
        sql = _stripped()
        assert "CREATE OR REPLACE FUNCTION public.claim_intel_run_tasks" in sql
        block = sql[sql.index("claim_intel_run_tasks"):]
        assert "FOR UPDATE SKIP LOCKED" in block

    def test_claim_is_a_lease_and_increments_attempts(self):
        sql = _stripped()
        block = sql[sql.index("claim_intel_run_tasks"):sql.index(
            "complete_intel_run_task"
        )]
        assert "lease_expires_at" in block
        assert "attempts         = t.attempts + 1" in block
        assert "t.attempts < t.max_attempts" in block
        # Expired leases are reclaimable.
        assert "lease_expires_at <= now()" in block

    def test_complete_guarded_by_owner_and_claimed_state(self):
        sql = _stripped()
        block = sql[sql.index("complete_intel_run_task"):]
        assert "AND state = 'claimed'" in block
        assert "AND claim_owner = p_worker_id" in block

    def test_claim_generation_fence(self):
        """Every claim mints a fresh claim_token; completion requires the
        CURRENT token — a stale (reclaimed) worker matches zero rows."""
        sql = _stripped()
        assert re.search(r"^\s+claim_token\s+UUID", sql, re.M), (
            "intel_run_tasks missing claim_token column"
        )
        claim_block = sql[sql.index("claim_intel_run_tasks"):sql.index(
            "complete_intel_run_task"
        )]
        assert "claim_token      = gen_random_uuid()" in claim_block
        complete_block = sql[sql.index("complete_intel_run_task"):]
        assert "p_claim_token" in complete_block
        assert "AND claim_token = p_claim_token" in complete_block

    def test_rpc_grants_are_service_role_only(self):
        sql = _stripped()
        assert re.search(r"REVOKE ALL ON FUNCTION public\.claim_intel_run_tasks", sql)
        assert re.search(
            r"GRANT EXECUTE ON FUNCTION public\.claim_intel_run_tasks[\s\S]{0,120}TO service_role",
            sql,
        )


class TestSpecialistOutputsTable:
    def test_table_unique_key_and_rls(self):
        sql = _stripped()
        assert (
            "CREATE TABLE IF NOT EXISTS public.intel_run_specialist_outputs"
            in sql
        )
        assert "uq_intel_run_specialist_outputs_key" in sql
        assert "intel_run_specialist_outputs_service_only" in sql
        assert "trg_intel_run_specialist_outputs_owner_guard" in sql

    def test_output_contract_columns(self):
        sql = _stripped()
        for column in (
            "stance", "score", "confidence", "key_findings", "risks",
            "evidence_refs", "missing_evidence", "limitations", "valid_until",
            "model", "prompt_version", "input_fingerprint", "batch_key",
        ):
            assert re.search(rf"^\s+{column}\s", sql, re.M), (
                f"intel_run_specialist_outputs missing column {column}"
            )


class TestIdempotencyAndSafety:
    def test_all_creates_guarded(self):
        sql = _stripped()
        for match in re.finditer(r"CREATE TABLE\s+(?!IF NOT EXISTS)", sql):
            raise AssertionError(f"unguarded CREATE TABLE at {match.start()}")
        for match in re.finditer(r"CREATE (UNIQUE )?INDEX\s+(?!IF NOT EXISTS)", sql):
            raise AssertionError(f"unguarded CREATE INDEX at {match.start()}")

    def test_no_destructive_statements(self):
        sql = _stripped()
        assert "TRUNCATE" not in sql
        assert not re.search(r"^\s*DELETE FROM", sql, re.M)
        # DROP is allowed only for the guarded policy/trigger/constraint swaps.
        for match in re.finditer(r"DROP (TABLE|COLUMN)", sql):
            raise AssertionError("migration must not drop tables/columns")


class TestRetentionCleanup:
    def test_new_tables_covered(self):
        sql = RETENTION.read_text(encoding="utf-8")
        assert "intel_run_tasks" in sql
        assert "intel_run_tickers" in sql
        assert "intel_run_specialist_outputs" in sql

    def test_active_sessions_preserved(self):
        sql = RETENTION.read_text(encoding="utf-8")
        # Terminal-only session delete must include the new terminal states
        # and still never delete active ones.
        match = re.search(
            r"DELETE FROM public\.intel_run_sessions\s*\nWHERE status IN \(([^)]*)\)",
            sql,
        )
        assert match, "session retention delete missing"
        states = match.group(1)
        assert "'completed'" in states
        assert "'failed'" in states
        assert "'completed_with_gaps'" in states
        assert "'superseded'" in states
        assert "'running'" not in states
