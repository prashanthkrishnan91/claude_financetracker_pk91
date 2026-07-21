-- ============================================================================
-- Intel v3 — distributed Run Intel workflow (durable task graph)
-- 027_intel_run_distributed_tasks.sql
-- ============================================================================
-- Additive + idempotent: safe to re-run (IF NOT EXISTS / DO $$ guards).
--
-- WHY: the session-era Run Intel execution (migration 026) still executed the
-- portfolio-wide analyst-refresh pipeline inside bounded 20-second HTTP
-- requests driven by browser continuations. Production session
-- 83f28044-f19c-4640-ab2d-14991db4e29d showed the failure shape: 32 ticker
-- jobs, a 3-ticker batch that spent its entire request deadline on
-- portfolio-wide preprocessing (market bundle, context, snapshots, features,
-- scorecards) and never reached an LLM, then the frontend treated one
-- zero-success batch as a terminal run failure with 29 jobs never attempted.
--
-- This migration makes Run Intel a durable task graph executed by a backend
-- worker supervisor:
--   * intel_run_tickers — one row per (session, active holding): the frozen,
--     immutable portfolio scope for the run, plus per-ticker workflow state.
--   * intel_run_tasks — a generic durable task queue (ticker-, batch- and
--     portfolio-scoped work) with atomic claiming, leases, retries and
--     idempotent task identity.
--   * claim_intel_run_tasks() — FOR UPDATE SKIP LOCKED claim RPC so multiple
--     workers can never execute the same task concurrently.
--   * intel_run_sessions gains the distributed-workflow states
--     ('running', 'completed_with_gaps', 'superseded'), a workflow_version
--     column, a current_stage column and a metrics column.
--   * Legacy supersession: unfinished workflow_version=1 sessions are marked
--     'superseded' (kept for audit, never rewritten as successful) so the new
--     worker can never accidentally adopt them.
--
-- MANUAL ACTION REQUIRED (Supabase) — this migration is NOT auto-applied:
--   1. Open the Supabase production project → SQL Editor → New query.
--   2. Paste the contents of this file and Run (in a maintenance window).
--   3. Verify (each should succeed; 0 rows is OK):
--        SELECT * FROM public.intel_run_tickers LIMIT 1;
--        SELECT * FROM public.intel_run_tasks LIMIT 1;
--        SELECT workflow_version, current_stage FROM public.intel_run_sessions LIMIT 1;
--        SELECT proname FROM pg_proc WHERE proname = 'claim_intel_run_tasks';
--   Until applied, the distributed Run Intel path degrades safely with an
--   explicit retryable error at session creation (table missing); no legacy
--   behavior is affected.
-- ============================================================================

-- ── 1. intel_run_sessions — distributed-workflow extensions ──────────────────

-- workflow_version: 1 = legacy bounded-drain sessions (migration 026 era),
-- 2 = distributed task-graph sessions (this migration). The worker supervisor
-- only ever touches workflow_version >= 2 sessions.
ALTER TABLE public.intel_run_sessions
    ADD COLUMN IF NOT EXISTS workflow_version INTEGER NOT NULL DEFAULT 1;

-- current_stage: coarse plain-English-mappable stage for the status plane
-- (preparing | collecting_evidence | specialist_analysis | deciding |
--  publishing | done). Presentation state only — never execution authority.
ALTER TABLE public.intel_run_sessions
    ADD COLUMN IF NOT EXISTS current_stage TEXT;

-- metrics: per-session cost/observability accounting (provider calls by lane,
-- cache hits, LLM calls by specialist, task counts, stage durations, token
-- estimates). Written by the worker; read by diagnostics. Never decision input.
ALTER TABLE public.intel_run_sessions
    ADD COLUMN IF NOT EXISTS metrics JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Extend the session state machine with the distributed-workflow states.
--   running             — task graph active (collectors/specialists/decisions)
--   completed_with_gaps — published, but some tickers degraded / NO CALL
--   superseded          — unfinished legacy (workflow_version=1) session
--                         marked terminally superseded by this migration or a
--                         newer explicit run. Kept for audit; never rewritten
--                         as successful; its snapshots are never reused.
DO $$
BEGIN
    ALTER TABLE public.intel_run_sessions
        DROP CONSTRAINT IF EXISTS intel_run_sessions_status_check;
    ALTER TABLE public.intel_run_sessions
        ADD CONSTRAINT intel_run_sessions_status_check CHECK (status IN (
            'created',
            'ticker_refresh_in_progress',
            'publishing',
            'publication_retryable_failed',
            'completed',
            'failed',
            -- distributed workflow (workflow_version >= 2)
            'running',
            'completed_with_gaps',
            'superseded'
        ));
END $$;

-- One active (non-terminal) distributed session per user at a time: the
-- product rule is "a later explicit click creates a new session only after
-- the prior session is terminal". Enforced at the database so two overlapping
-- browser tabs cannot create concurrent active runs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_intel_run_sessions_active_per_user
    ON public.intel_run_sessions (user_id)
    WHERE workflow_version >= 2
      AND status NOT IN ('completed', 'completed_with_gaps', 'failed', 'superseded');

-- ── 2. Legacy supersession ───────────────────────────────────────────────────
-- Unfinished legacy sessions must never be claimed or continued by the new
-- worker. Mark them terminally superseded (kept for audit; never successful).
UPDATE public.intel_run_sessions
SET status = 'superseded',
    last_error = COALESCE(last_error,
        'superseded_by_distributed_workflow_migration_027'),
    updated_at = now()
WHERE workflow_version = 1
  AND status IN (
      'created',
      'ticker_refresh_in_progress',
      'publishing',
      'publication_retryable_failed'
  );

-- ── 3. intel_run_tickers — frozen per-session portfolio scope ────────────────
--
-- One row per (run session, active holding) captured at session creation.
-- This is the immutable truth the rest of the run reproduces from: later
-- position edits never change a running session's scope.
CREATE TABLE IF NOT EXISTS public.intel_run_tickers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_session_id      UUID NOT NULL
                            REFERENCES public.intel_run_sessions(id)
                            ON DELETE CASCADE,
    user_id             UUID NOT NULL,
    ticker              TEXT NOT NULL,
    -- equity | etf | crypto (mapped from positions.category at freeze time)
    asset_type          TEXT NOT NULL DEFAULT 'equity',
    -- Frozen portfolio truth (immutable for this run; reproducibility inputs)
    quantity            DOUBLE PRECISION,
    market_value        DOUBLE PRECISION,
    portfolio_weight_pct DOUBLE PRECISION,
    cost_basis          DOUBLE PRECISION,
    unrealized_gain_pct DOUBLE PRECISION,
    -- Compact tax summary (lt_eligible, lt_date, days_to_long_term) — a
    -- summary, not a duplicate of the tax-lot engine's tables.
    tax_summary         JSONB NOT NULL DEFAULT '{}'::jsonb,
    prior_action        TEXT,
    -- Execution ordering only — never scope exclusion.
    priority            INTEGER NOT NULL DEFAULT 100,
    -- Evidence lanes this ticker's asset type requires (JSON array of lane
    -- names). Frozen at scheduling time so mid-run config changes cannot
    -- redefine a running session.
    required_lanes      JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Ticker state machine (application-managed):
    --   pending            — created; evidence collection not finished
    --   evidence_ready     — all required lanes terminal; bundle built
    --   analysis_complete  — all required specialist axes terminal
    --   decision_ready     — deterministic decision input assembled
    --   decided            — deterministic policy produced a final action
    --   no_call            — policy produced NO CALL / EVIDENCE INCOMPLETE
    --   failed             — unrecoverable for this ticker only (never fails
    --                        the session by itself)
    state               TEXT NOT NULL DEFAULT 'pending',
    -- Honest degradation bookkeeping: lanes/axes that failed or were skipped,
    -- with reasons. Read by policy for confidence reduction / suppression.
    missing_lanes       JSONB NOT NULL DEFAULT '[]'::jsonb,
    degraded_lanes      JSONB NOT NULL DEFAULT '[]'::jsonb,
    degradation_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Immutable evidence bundle (built once when required lanes are terminal;
    -- the ONLY input specialists see) and the deterministic decision record
    -- (audit copy of decision_policy_v1.decide() output for this run).
    evidence_bundle     JSONB,
    decision            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (asset_type IN ('equity', 'etf', 'crypto')),
    CHECK (state IN (
        'pending',
        'evidence_ready',
        'analysis_complete',
        'decision_ready',
        'decided',
        'no_call',
        'failed'
    ))
);

-- Idempotency: a session cannot freeze the same ticker twice.
CREATE UNIQUE INDEX IF NOT EXISTS uq_intel_run_tickers_session_ticker
    ON public.intel_run_tickers (run_session_id, ticker);

CREATE INDEX IF NOT EXISTS idx_intel_run_tickers_session_state
    ON public.intel_run_tickers (run_session_id, state);

CREATE INDEX IF NOT EXISTS idx_intel_run_tickers_user
    ON public.intel_run_tickers (user_id, created_at DESC);

-- Cross-user link protection: a ticker row's user must match its session's
-- user. Service-role writes bypass RLS, so enforce with a trigger.
CREATE OR REPLACE FUNCTION public.intel_run_ticker_owner_guard()
RETURNS TRIGGER AS $$
DECLARE
    session_user UUID;
BEGIN
    SELECT user_id INTO session_user
    FROM public.intel_run_sessions
    WHERE id = NEW.run_session_id;
    IF session_user IS NULL THEN
        RAISE EXCEPTION 'intel_run_tickers: unknown run_session_id %',
            NEW.run_session_id;
    END IF;
    IF session_user <> NEW.user_id THEN
        RAISE EXCEPTION
            'intel_run_tickers: user_id % does not own session % (owner %)',
            NEW.user_id, NEW.run_session_id, session_user;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_intel_run_tickers_owner_guard
    ON public.intel_run_tickers;
CREATE TRIGGER trg_intel_run_tickers_owner_guard
    BEFORE INSERT OR UPDATE ON public.intel_run_tickers
    FOR EACH ROW EXECUTE FUNCTION public.intel_run_ticker_owner_guard();

-- Operational table — service-role only, deny-all RLS (migration 018/026
-- convention).
ALTER TABLE public.intel_run_tickers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS intel_run_tickers_service_only ON public.intel_run_tickers;
CREATE POLICY intel_run_tickers_service_only
    ON public.intel_run_tickers FOR ALL USING (false);

-- ── 4. intel_run_tasks — generic durable task queue ──────────────────────────
CREATE TABLE IF NOT EXISTS public.intel_run_tasks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_session_id      UUID NOT NULL
                            REFERENCES public.intel_run_sessions(id)
                            ON DELETE CASCADE,
    user_id             UUID NOT NULL,
    -- Scope: exactly one of ticker (ticker-scoped), batch_key (batch-scoped)
    -- or neither (portfolio/session-scoped).
    ticker              TEXT,
    batch_key           TEXT,
    -- Task taxonomy (application-owned; see RUN_INTEL_DISTRIBUTED_WORKFLOW.md):
    --   collect_portfolio_context | collect_macro_context |
    --   collect_evidence_lane | build_evidence_bundle |
    --   specialist_analysis | review_conflict |
    --   ticker_decision | portfolio_join_publish
    task_type           TEXT NOT NULL,
    -- Analytical/evidence lane (for collect_evidence_lane) or specialist axis
    -- (for specialist_analysis / review). NULL for scope-level tasks.
    lane                TEXT,
    asset_type          TEXT,
    -- Task state machine:
    --   blocked   — prerequisites not terminal; not claimable
    --   pending   — claimable when next_retry_at <= now()
    --   claimed   — leased by claim_owner until lease_expires_at
    --   succeeded — terminal success
    --   degraded  — terminal, produced partial/degraded output honestly
    --   failed    — terminal failure (attempts exhausted); isolates to its
    --               own lane/ticker/axis, never the session by itself
    --   cancelled — superseded (e.g. session cancelled)
    state               TEXT NOT NULL DEFAULT 'pending',
    priority            INTEGER NOT NULL DEFAULT 100,
    attempts            INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 3,
    -- Claiming / lease. claim_token is the claim-generation fence: a fresh
    -- UUID minted on EVERY claim. Completion and every task-owned side effect
    -- must present the current token, so a stale worker whose lease expired
    -- and whose task was reclaimed can never overwrite the new claim's work.
    claim_owner         TEXT,
    claim_token         UUID,
    claimed_at          TIMESTAMPTZ,
    lease_expires_at    TIMESTAMPTZ,
    next_retry_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Reuse / idempotency: hash of the task's semantic input. Unchanged
    -- fingerprints allow safe reuse (no duplicate provider/LLM work).
    input_fingerprint   TEXT,
    -- Reference to the durable output (research_artifacts id, evidence bundle
    -- id, specialist output id, snapshot row id, ...) — task-type specific.
    output_ref          TEXT,
    -- Normalized task output payload (e.g. a collector's normalized lane
    -- evidence when no research-artifact row is the natural home). Durable so
    -- bundle builds and retries never re-fetch successful work.
    output              JSONB,
    error_code          TEXT,
    error_detail        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (state IN (
        'blocked', 'pending', 'claimed',
        'succeeded', 'degraded', 'failed', 'cancelled'
    )),
    CHECK (asset_type IS NULL OR asset_type IN ('equity', 'etf', 'crypto'))
);

-- Idempotent logical task identity: one task per
-- (session, task_type, lane, ticker, batch_key) — NULLs normalised so the
-- uniqueness actually holds (Postgres treats NULLs as distinct in plain
-- unique indexes).
CREATE UNIQUE INDEX IF NOT EXISTS uq_intel_run_tasks_logical
    ON public.intel_run_tasks (
        run_session_id,
        task_type,
        COALESCE(lane, ''),
        COALESCE(ticker, ''),
        COALESCE(batch_key, '')
    );

-- Claim path: due pending tasks by priority then age.
CREATE INDEX IF NOT EXISTS idx_intel_run_tasks_claimable
    ON public.intel_run_tasks (run_session_id, state, next_retry_at, priority)
    WHERE state IN ('pending', 'claimed');

CREATE INDEX IF NOT EXISTS idx_intel_run_tasks_session_state
    ON public.intel_run_tasks (run_session_id, state);

CREATE INDEX IF NOT EXISTS idx_intel_run_tasks_user
    ON public.intel_run_tasks (user_id, created_at DESC);

-- Cross-user link protection (same rationale as intel_run_tickers).
CREATE OR REPLACE FUNCTION public.intel_run_task_owner_guard()
RETURNS TRIGGER AS $$
DECLARE
    session_user UUID;
BEGIN
    SELECT user_id INTO session_user
    FROM public.intel_run_sessions
    WHERE id = NEW.run_session_id;
    IF session_user IS NULL THEN
        RAISE EXCEPTION 'intel_run_tasks: unknown run_session_id %',
            NEW.run_session_id;
    END IF;
    IF session_user <> NEW.user_id THEN
        RAISE EXCEPTION
            'intel_run_tasks: user_id % does not own session % (owner %)',
            NEW.user_id, NEW.run_session_id, session_user;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_intel_run_tasks_owner_guard
    ON public.intel_run_tasks;
CREATE TRIGGER trg_intel_run_tasks_owner_guard
    BEFORE INSERT OR UPDATE ON public.intel_run_tasks
    FOR EACH ROW EXECUTE FUNCTION public.intel_run_task_owner_guard();

ALTER TABLE public.intel_run_tasks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS intel_run_tasks_service_only ON public.intel_run_tasks;
CREATE POLICY intel_run_tasks_service_only
    ON public.intel_run_tasks FOR ALL USING (false);

-- ── 5. Atomic claim RPC ──────────────────────────────────────────────────────
--
-- FOR UPDATE SKIP LOCKED claim: multiple workers can call this concurrently
-- and can never receive the same task. A claim is a lease — a worker that
-- dies never blocks the task forever; once lease_expires_at passes, the task
-- is claimable again (attempts already incremented at claim time so a
-- crash-looping task still exhausts max_attempts).
CREATE OR REPLACE FUNCTION public.claim_intel_run_tasks(
    p_worker_id      TEXT,
    p_limit          INTEGER DEFAULT 1,
    p_lease_seconds  INTEGER DEFAULT 300,
    p_run_session_id UUID DEFAULT NULL
)
RETURNS SETOF public.intel_run_tasks AS $$
BEGIN
    RETURN QUERY
    WITH candidate AS (
        SELECT t.id
        FROM public.intel_run_tasks t
        WHERE (p_run_session_id IS NULL OR t.run_session_id = p_run_session_id)
          AND (
                (t.state = 'pending' AND t.next_retry_at <= now())
             OR (t.state = 'claimed' AND t.lease_expires_at IS NOT NULL
                 AND t.lease_expires_at <= now())
          )
          AND t.attempts < t.max_attempts
        ORDER BY t.priority ASC, t.next_retry_at ASC, t.created_at ASC
        LIMIT GREATEST(p_limit, 0)
        FOR UPDATE SKIP LOCKED
    )
    UPDATE public.intel_run_tasks t
    SET state            = 'claimed',
        claim_owner      = p_worker_id,
        claim_token      = gen_random_uuid(),
        claimed_at       = now(),
        started_at       = COALESCE(t.started_at, now()),
        lease_expires_at = now() + make_interval(secs => p_lease_seconds),
        attempts         = t.attempts + 1,
        updated_at       = now()
    FROM candidate c
    WHERE t.id = c.id
    RETURNING t.*;
END;
$$ LANGUAGE plpgsql;

-- Completion guard: a task can only be completed by its current claim owner
-- presenting the CURRENT claim token, and only from 'claimed' — the same
-- task can never be completed twice, and a stale (reclaimed) worker's late
-- completion matches zero rows.
CREATE OR REPLACE FUNCTION public.complete_intel_run_task(
    p_task_id      UUID,
    p_worker_id    TEXT,
    p_claim_token  UUID,
    p_final_state  TEXT,
    p_output_ref   TEXT DEFAULT NULL,
    p_error_code   TEXT DEFAULT NULL,
    p_error_detail TEXT DEFAULT NULL,
    p_retry_at     TIMESTAMPTZ DEFAULT NULL
)
RETURNS BOOLEAN AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    IF p_final_state NOT IN ('succeeded', 'degraded', 'failed', 'pending') THEN
        RAISE EXCEPTION 'complete_intel_run_task: illegal final state %',
            p_final_state;
    END IF;
    UPDATE public.intel_run_tasks
    SET state            = p_final_state,
        output_ref       = COALESCE(p_output_ref, output_ref),
        error_code       = p_error_code,
        error_detail     = p_error_detail,
        completed_at     = CASE WHEN p_final_state IN
                               ('succeeded', 'degraded', 'failed')
                               THEN now() ELSE NULL END,
        next_retry_at    = COALESCE(p_retry_at, next_retry_at),
        claim_owner      = CASE WHEN p_final_state = 'pending'
                               THEN NULL ELSE claim_owner END,
        lease_expires_at = NULL,
        updated_at       = now()
    WHERE id = p_task_id
      AND state = 'claimed'
      AND claim_owner = p_worker_id
      AND claim_token = p_claim_token;
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count > 0;
END;
$$ LANGUAGE plpgsql;

-- Service-role only execution (PostgREST exposes RPC to service key; the
-- deny-all RLS tables are only reachable through the backend anyway).
REVOKE ALL ON FUNCTION public.claim_intel_run_tasks(TEXT, INTEGER, INTEGER, UUID)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.complete_intel_run_task(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_intel_run_tasks(TEXT, INTEGER, INTEGER, UUID)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.complete_intel_run_task(
    UUID, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ) TO service_role;

-- ── 6. Specialist outputs — one row per (session, ticker, axis) ──────────────
--
-- Independently addressable specialist results, persisted per ticker even
-- when the LLM call was batched. Advisory research output only — deterministic
-- policy reads it as ONE input; it never carries visible action authority.
CREATE TABLE IF NOT EXISTS public.intel_run_specialist_outputs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_session_id    UUID NOT NULL
                          REFERENCES public.intel_run_sessions(id)
                          ON DELETE CASCADE,
    user_id           UUID NOT NULL,
    ticker            TEXT NOT NULL,
    -- Specialist axis: fundamental | technical | sentiment | risk_filing |
    -- etf_exposure | crypto_market | review
    axis              TEXT NOT NULL,
    stance            TEXT,
    score             DOUBLE PRECISION,
    confidence        DOUBLE PRECISION,
    key_findings      JSONB NOT NULL DEFAULT '[]'::jsonb,
    risks             JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_refs     JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_evidence  JSONB NOT NULL DEFAULT '[]'::jsonb,
    limitations       JSONB NOT NULL DEFAULT '[]'::jsonb,
    valid_until       TIMESTAMPTZ,
    model             TEXT,
    prompt_version    TEXT,
    input_fingerprint TEXT,
    batch_key         TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (axis IN (
        'fundamental', 'technical', 'sentiment', 'risk_filing',
        'etf_exposure', 'crypto_market', 'review'
    ))
);

-- One output per (session, ticker, axis): repair retries overwrite via
-- upsert, they never duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS uq_intel_run_specialist_outputs_key
    ON public.intel_run_specialist_outputs (run_session_id, ticker, axis);

CREATE INDEX IF NOT EXISTS idx_intel_run_specialist_outputs_session
    ON public.intel_run_specialist_outputs (run_session_id, ticker);

-- Fingerprint reuse lookup: unchanged evidence for the same user/ticker/axis
-- may reuse a prior session's still-valid output instead of a new LLM call.
CREATE INDEX IF NOT EXISTS idx_intel_run_specialist_outputs_reuse
    ON public.intel_run_specialist_outputs
    (user_id, ticker, axis, input_fingerprint, created_at DESC);

DROP TRIGGER IF EXISTS trg_intel_run_specialist_outputs_owner_guard
    ON public.intel_run_specialist_outputs;
CREATE TRIGGER trg_intel_run_specialist_outputs_owner_guard
    BEFORE INSERT OR UPDATE ON public.intel_run_specialist_outputs
    FOR EACH ROW EXECUTE FUNCTION public.intel_run_task_owner_guard();

ALTER TABLE public.intel_run_specialist_outputs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS intel_run_specialist_outputs_service_only
    ON public.intel_run_specialist_outputs;
CREATE POLICY intel_run_specialist_outputs_service_only
    ON public.intel_run_specialist_outputs FOR ALL USING (false);

-- ── ROLLBACK (commented out by default) ──────────────────────────────────────
-- DROP TABLE IF EXISTS public.intel_run_specialist_outputs CASCADE;
-- DROP FUNCTION IF EXISTS public.complete_intel_run_task(
--     UUID, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ);
-- DROP FUNCTION IF EXISTS public.claim_intel_run_tasks(TEXT, INTEGER, INTEGER, UUID);
-- DROP TABLE IF EXISTS public.intel_run_tasks CASCADE;
-- DROP FUNCTION IF EXISTS public.intel_run_task_owner_guard();
-- DROP TABLE IF EXISTS public.intel_run_tickers CASCADE;
-- DROP FUNCTION IF EXISTS public.intel_run_ticker_owner_guard();
-- DROP INDEX IF EXISTS uq_intel_run_sessions_active_per_user;
-- ALTER TABLE public.intel_run_sessions DROP COLUMN IF EXISTS metrics;
-- ALTER TABLE public.intel_run_sessions DROP COLUMN IF EXISTS current_stage;
-- ALTER TABLE public.intel_run_sessions DROP COLUMN IF EXISTS workflow_version;
-- (status CHECK retains the extended value set; harmless for legacy rows)
