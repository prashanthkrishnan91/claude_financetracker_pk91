-- ============================================================================
-- Stage 3A — Action Feedback Foundation v1
-- action_feedback_events — append-only user feedback on Intel/Deploy/Watchtower actions
-- ============================================================================
-- Additive migration — does NOT modify any existing table.
-- Safe to re-run: all statements use IF NOT EXISTS / DO $$ guards.
--
-- WHY: Gives Intel/Deploy/Watchtower a deterministic user-feedback memory
-- layer before full alert delivery or UI redesign. Feedback is stored
-- evidence/context only — it does NOT mutate Intel v3 decisions, Deploy
-- sizing, Watchtower refresh behavior, or broker/execution behavior.
--
-- MANUAL ACTION REQUIRED (Supabase) — this migration is NOT auto-applied:
--   1. Open the Supabase production project → SQL Editor → New query.
--   2. Paste the contents of this file and Run.
--   3. Verify: SELECT COUNT(*) FROM public.action_feedback_events;  (0 rows is OK)
--   Until applied, both action-feedback endpoints (POST and GET) will return 500
--   because the table does not exist. No other app behavior is affected
--   (Intel v3, Deploy, Watchtower unchanged).
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.action_feedback_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,

    -- Feedback classification
    feedback_type       TEXT NOT NULL,
    -- Valid values: executed | skipped | ignored | snoozed | too_risky | not_relevant | user_note

    -- Source area of the actionable item that prompted this feedback
    source_area         TEXT NOT NULL,
    -- Valid values: intel | deploy | watchtower | alert

    -- Context — all nullable depending on source area
    ticker              TEXT,                  -- normalized to uppercase by service layer
    action_type         TEXT,                  -- BUY | HOLD | TRIM | SELL | DEPLOY_ACTION

    -- Run/snapshot identifiers — optional, tied to a specific run when available
    agent_run_id        UUID,
    snapshot_id         UUID,

    -- Optional plain-English note
    note                TEXT,

    -- Idempotency: client-provided key prevents duplicate submits.
    -- Recommended format: "{source_area}:{ticker}:{action_type}:{run_id_or_date}"
    idempotency_key     TEXT NOT NULL,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_feedback_type CHECK (
        feedback_type IN ('executed', 'skipped', 'ignored', 'snoozed', 'too_risky', 'not_relevant', 'user_note')
    ),
    CONSTRAINT chk_source_area CHECK (
        source_area IN ('intel', 'deploy', 'watchtower', 'alert')
    )
);

-- Idempotency: exactly one feedback event per (user, idempotency_key).
-- Repeated client submits with the same key return the existing row.
CREATE UNIQUE INDEX IF NOT EXISTS uq_action_feedback_user_idem_key
    ON public.action_feedback_events (user_id, idempotency_key);

-- Per-user chronological lookup (most recent first)
CREATE INDEX IF NOT EXISTS idx_action_feedback_user_created
    ON public.action_feedback_events (user_id, created_at DESC);

-- Per-user/ticker lookup for history queries
CREATE INDEX IF NOT EXISTS idx_action_feedback_user_ticker
    ON public.action_feedback_events (user_id, ticker, created_at DESC);

-- Per-user/source_area lookup
CREATE INDEX IF NOT EXISTS idx_action_feedback_user_source
    ON public.action_feedback_events (user_id, source_area, created_at DESC);

-- RLS: authenticated users can read/write their own rows only.
-- The service role bypasses RLS for internal operations.
ALTER TABLE public.action_feedback_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS action_feedback_events_user_policy ON public.action_feedback_events;
CREATE POLICY action_feedback_events_user_policy
    ON public.action_feedback_events FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- ── ROLLBACK (commented out by default) ──────────────────────────────────────
-- DROP TABLE IF EXISTS public.action_feedback_events CASCADE;
