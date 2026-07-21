-- ============================================================================
-- Intel v3 — durable Run Intel sessions
-- 026_intel_run_sessions.sql
-- ============================================================================
-- Additive + idempotent: safe to re-run (IF NOT EXISTS / DO $$ guards).
--
-- WHY: one explicit Run Intel click needs one durable, SQL-backed identity.
-- Before this migration, analyst_refresh_jobs rows and intel_v3_snapshots
-- rows had no explicit relationship to the click that created them — old
-- jobs could satisfy or block a new click, and completion was inferred from
-- whichever snapshot happened to be newest. intel_run_sessions makes the
-- click itself a durable row; jobs and snapshots reference it by FK.
--
-- MANUAL ACTION REQUIRED (Supabase) — this migration is NOT auto-applied:
--   1. Open the Supabase production project → SQL Editor → New query.
--   2. Paste the contents of this file and Run (in a maintenance window).
--   3. Verify:
--        SELECT * FROM public.intel_run_sessions LIMIT 1;          -- 0 rows OK
--        SELECT run_session_id FROM public.analyst_refresh_jobs LIMIT 1;
--        SELECT run_session_id FROM public.intel_v3_snapshots LIMIT 1;
--   Until applied, the session-aware Run Intel path degrades safely with an
--   explicit retryable error (session insert fails); legacy null-session
--   queue behavior is unaffected.
-- ============================================================================

-- ── 1. intel_run_sessions — one row per explicit Run Intel click ─────────────
--
-- Retention/deletion semantics (kept in sync with
-- cost_guard_retention_cleanup.sql):
--   * Deleting an intel_v3_snapshots row NULLs any session pointer to it
--     (pre_session_snapshot_id / completed_snapshot_id → ON DELETE SET NULL),
--     so snapshot retention cleanup can never fail on a session reference.
--   * Deleting a session CASCADE-deletes its own analyst_refresh_jobs rows
--     (session jobs are that session's bookkeeping) and NULLs
--     intel_v3_snapshots.run_session_id (the snapshot outlives the session
--     record until its own retention window).
--
-- The id is supplied by the client (browser crypto.randomUUID() per manual
-- click) so network retries and automatic continuations of the same click
-- share one identity. gen_random_uuid() default covers legacy callers that
-- let the backend mint the id.
CREATE TABLE IF NOT EXISTS public.intel_run_sessions (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                   UUID NOT NULL,
    -- Session state machine (application-managed):
    --   created                      — row inserted; jobs being enqueued
    --   ticker_refresh_in_progress   — session jobs enqueued; refresh running
    --   publishing                   — all required ticker jobs succeeded;
    --                                  deterministic certification/publication
    --                                  pending or in progress
    --   publication_retryable_failed — certification/publication failed; every
    --                                  ticker job stays succeeded; the next
    --                                  continuation retries publication only
    --   completed                    — this session's own snapshot published
    --   failed                       — terminal (e.g. a session job exhausted
    --                                  its retry budget)
    status                    TEXT NOT NULL DEFAULT 'created',
    -- Immutable active-holdings scope captured when the click begins.
    holdings_scope            JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Subset of holdings_scope requiring analyst refresh at click time.
    stale_tickers             JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Exactly one session job per stale ticker.
    expected_ticker_job_count INTEGER NOT NULL DEFAULT 0,
    -- Latest active snapshot ROW id (intel_v3_snapshots.id, UUID) at click
    -- time. Completion requires a DIFFERENT, session-linked snapshot row.
    pre_session_snapshot_id   UUID NULL REFERENCES public.intel_v3_snapshots(id)
                                  ON DELETE SET NULL,
    -- The snapshot row published for THIS session (set on completion).
    completed_snapshot_id     UUID NULL REFERENCES public.intel_v3_snapshots(id)
                                  ON DELETE SET NULL,
    -- Retryable error information (publication failures etc.).
    last_error                TEXT,
    publication_attempts      INTEGER NOT NULL DEFAULT 0,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at              TIMESTAMPTZ,
    CHECK (status IN (
        'created',
        'ticker_refresh_in_progress',
        'publishing',
        'publication_retryable_failed',
        'completed',
        'failed'
    ))
);

-- Per-user session lookup (ownership checks, diagnostics).
CREATE INDEX IF NOT EXISTS idx_intel_run_sessions_user
    ON public.intel_run_sessions (user_id, created_at DESC);

-- Operational table — service-role writes only, mirroring analyst_refresh_jobs
-- (migration 018). RLS enabled with a deny-all policy; the backend service
-- role bypasses RLS.
ALTER TABLE public.intel_run_sessions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS intel_run_sessions_service_only ON public.intel_run_sessions;
CREATE POLICY intel_run_sessions_service_only
    ON public.intel_run_sessions FOR ALL USING (false);

-- ── 2. analyst_refresh_jobs.run_session_id ───────────────────────────────────
--
-- New Run Intel jobs always carry the session id; historical rows stay NULL
-- and keep the legacy per-UTC-day idempotency behavior.
ALTER TABLE public.analyst_refresh_jobs
    ADD COLUMN IF NOT EXISTS run_session_id UUID NULL
        REFERENCES public.intel_run_sessions(id) ON DELETE CASCADE;

-- Replace the old daily-window uniqueness so a second same-day session can
-- coexist. The legacy rule is preserved for null-session rows only.
DROP INDEX IF EXISTS uq_analyst_refresh_jobs_user_ticker_window;

-- One job per (session, ticker) for session-aware jobs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_analyst_refresh_jobs_session_ticker
    ON public.analyst_refresh_jobs (run_session_id, ticker)
    WHERE run_session_id IS NOT NULL;

-- Legacy idempotency preserved for pre-session rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_analyst_refresh_jobs_legacy_window
    ON public.analyst_refresh_jobs (user_id, ticker, refresh_window)
    WHERE run_session_id IS NULL;

-- Session-oriented claim/count path.
CREATE INDEX IF NOT EXISTS idx_analyst_refresh_jobs_session_status
    ON public.analyst_refresh_jobs (run_session_id, status)
    WHERE run_session_id IS NOT NULL;

-- ── 3. intel_v3_snapshots.run_session_id ─────────────────────────────────────
--
-- Every snapshot published for a Run Intel session carries the exact session
-- id in this scalar column AND in payload->>'run_session_id'. Historical
-- snapshots stay NULL.
ALTER TABLE public.intel_v3_snapshots
    ADD COLUMN IF NOT EXISTS run_session_id UUID NULL
        REFERENCES public.intel_run_sessions(id) ON DELETE SET NULL;

-- Publication idempotency: retrying publication can never create a second
-- snapshot row for the same session.
CREATE UNIQUE INDEX IF NOT EXISTS uq_intel_v3_snapshots_run_session
    ON public.intel_v3_snapshots (run_session_id)
    WHERE run_session_id IS NOT NULL;

-- ── ROLLBACK (commented out by default) ──────────────────────────────────────
-- DROP INDEX IF EXISTS uq_intel_v3_snapshots_run_session;
-- ALTER TABLE public.intel_v3_snapshots DROP COLUMN IF EXISTS run_session_id;
-- DROP INDEX IF EXISTS idx_analyst_refresh_jobs_session_status;
-- DROP INDEX IF EXISTS uq_analyst_refresh_jobs_legacy_window;
-- DROP INDEX IF EXISTS uq_analyst_refresh_jobs_session_ticker;
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_analyst_refresh_jobs_user_ticker_window
--     ON public.analyst_refresh_jobs (user_id, ticker, refresh_window);
-- ALTER TABLE public.analyst_refresh_jobs DROP COLUMN IF EXISTS run_session_id;
-- DROP TABLE IF EXISTS public.intel_run_sessions CASCADE;
