-- ============================================================================
-- Intel v3 Stage 3.2 — Continuous Intelligence Plane v1
-- analyst_refresh_jobs — durable owned-position analyst evidence refresh queue
-- ============================================================================
-- Additive migration — does NOT modify any existing table.
-- Safe to re-run: all statements use IF NOT EXISTS / DO $$ guards.
--
-- WHY: Stage 3.1 decoupled the synchronous Run Intel v3 HTTP request so it
-- never performs analyst/LLM refresh inside the click — the request seam only
-- *logged* that stale analyst evidence needed a refresh. Stage 3.2 makes that
-- request durable: the seam upserts a row here, and a separate background
-- worker (analyst_refresh_worker_v1) consumes due rows and drives the existing
-- full-portfolio analyst adapter outside the HTTP request.
--
-- MANUAL ACTION REQUIRED (Supabase) — this migration is NOT auto-applied:
--   1. Open the Supabase production project → SQL Editor → New query.
--   2. Paste the contents of this file and Run (in a maintenance window).
--   3. Verify: SELECT * FROM public.analyst_refresh_jobs LIMIT 1;  (0 rows is OK)
--   Until applied, Stage 3.2 degrades safely: the seam still logs + returns
--   refresh_requested, and the worker reports zero due jobs (never crashes).
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.analyst_refresh_jobs (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                       UUID NOT NULL,
    ticker                        TEXT NOT NULL,
    -- Idempotency window (per-UTC-day key): repeated Run Intel v3 clicks for
    -- the same user/ticker inside the same window collapse onto a single row.
    -- The application enqueue is idempotent per (user_id, ticker,
    -- refresh_window): a non-terminal row (pending/claimed/retryable-failed)
    -- is left in place; a terminal/dead row (succeeded, or failed with
    -- attempts exhausted) is REOPENED in place to pending when the ticker is
    -- still stale — so a legitimate same-window retry is never silently
    -- suppressed, while the row count per key stays exactly one.
    refresh_window                TEXT NOT NULL,
    status                        TEXT NOT NULL DEFAULT 'pending',  -- pending|claimed|succeeded|failed
    attempts                      INTEGER NOT NULL DEFAULT 0,
    max_attempts                  INTEGER NOT NULL DEFAULT 5,
    -- Selection hints captured at request time so the worker can prioritise
    -- owned BUY/TRIM positions first without re-querying.
    prior_action                  TEXT,
    weight_pct                    DOUBLE PRECISION,
    evidence_age_hours_at_request  DOUBLE PRECISION,
    worker_run_id                 UUID,
    last_error                    TEXT,
    requested_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at                    TIMESTAMPTZ,
    completed_at                  TIMESTAMPTZ,
    -- next_retry_at: when the job becomes eligible for the worker to claim.
    -- pending jobs are due immediately; failed jobs back off exponentially.
    next_retry_at                 TIMESTAMPTZ,
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN ('pending', 'claimed', 'succeeded', 'failed'))
);

-- Idempotency: exactly one job per (user, ticker, window). The Stage 3.1
-- refresh-request seam checks this key before inserting so repeated clicks
-- never spawn duplicate jobs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_analyst_refresh_jobs_user_ticker_window
    ON public.analyst_refresh_jobs (user_id, ticker, refresh_window);

-- Due-job selection: the worker claims pending/failed jobs whose retry time
-- has arrived, oldest request first.
CREATE INDEX IF NOT EXISTS idx_analyst_refresh_jobs_due
    ON public.analyst_refresh_jobs (status, next_retry_at, requested_at);

-- Per-user lookup for observability / diagnostics.
CREATE INDEX IF NOT EXISTS idx_analyst_refresh_jobs_user
    ON public.analyst_refresh_jobs (user_id, status);

-- Operational table — service-role writes only, mirroring provider_health /
-- api_call_ledger (migration 007). RLS is enabled with a deny-all policy so
-- the anon / user roles cannot read it; the backend service role bypasses RLS.
ALTER TABLE public.analyst_refresh_jobs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS analyst_refresh_jobs_service_only ON public.analyst_refresh_jobs;
CREATE POLICY analyst_refresh_jobs_service_only
    ON public.analyst_refresh_jobs FOR ALL USING (false);

-- ── ROLLBACK (commented out by default) ──────────────────────────────────────
-- DROP TABLE IF EXISTS public.analyst_refresh_jobs CASCADE;
