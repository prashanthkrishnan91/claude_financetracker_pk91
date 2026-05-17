-- ============================================================================
-- Stage 3B — Alert Trigger Policy v1
-- watchtower_alert_candidates — deterministic alert candidate store
-- ============================================================================
-- Additive migration — does NOT modify any existing table.
-- Safe to re-run: all statements use IF NOT EXISTS guards.
--
-- WHY: Stage 3B adds the deterministic alert-worthiness layer before any
-- email/push delivery. Candidates are created by alert_trigger_policy_v1.py
-- based on Intel v3 snapshot action changes and user feedback history.
-- No mutations to intel_v3_snapshots, Deploy, Watchtower, or feedback rows.
--
-- MANUAL ACTION REQUIRED (Supabase) — this migration is NOT auto-applied:
--   1. Open the Supabase production project → SQL Editor → New query.
--   2. Paste the contents of this file and Run.
--   3. Verify: SELECT COUNT(*) FROM public.watchtower_alert_candidates;  (0 rows is OK)
--   Until applied, GET /api/v1/alert-candidates will return 500.
--   No other app behavior is affected (Intel v3, Deploy, Watchtower, feedback unchanged).
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.watchtower_alert_candidates (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL,

    -- What triggered this candidate
    ticker               TEXT NOT NULL,
    source_area          TEXT NOT NULL DEFAULT 'intel',
    -- Valid values: intel | deploy | watchtower

    candidate_type       TEXT NOT NULL,
    -- Valid values: new_actionable_action | conviction_upgrade | watchtower_refresh

    action_type          TEXT,
    -- Valid values: BUY | HOLD | TRIM | SELL | DEPLOY_ACTION or NULL

    severity             TEXT NOT NULL DEFAULT 'normal',
    -- Valid values: low | normal | high

    -- Human-readable and machine-readable reason
    reason_code          TEXT NOT NULL,
    plain_english_reason TEXT NOT NULL,

    -- Provenance — ties candidate to the source snapshot/run
    source_snapshot_id   UUID,
    source_run_id        UUID,
    policy_version       TEXT NOT NULL DEFAULT 'v1',

    -- Lifecycle
    status               TEXT NOT NULL DEFAULT 'candidate',
    -- Valid values: candidate | suppressed | dismissed | snoozed | expired

    -- Idempotency: one row per (user_id, dedupe_key)
    dedupe_key           TEXT NOT NULL,

    -- Optional timing fields for future snoozed/expiry handling
    expires_at           TIMESTAMPTZ,
    cooldown_until       TIMESTAMPTZ,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_alert_source_area CHECK (
        source_area IN ('intel', 'deploy', 'watchtower')
    ),
    CONSTRAINT chk_alert_candidate_type CHECK (
        candidate_type IN ('new_actionable_action', 'conviction_upgrade', 'watchtower_refresh')
    ),
    CONSTRAINT chk_alert_action_type CHECK (
        action_type IN ('BUY', 'HOLD', 'TRIM', 'SELL', 'DEPLOY_ACTION') OR action_type IS NULL
    ),
    CONSTRAINT chk_alert_severity CHECK (
        severity IN ('low', 'normal', 'high')
    ),
    CONSTRAINT chk_alert_status CHECK (
        status IN ('candidate', 'suppressed', 'dismissed', 'snoozed', 'expired')
    )
);

-- Idempotency: exactly one candidate per (user_id, dedupe_key).
CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_candidates_dedupe_key
    ON public.watchtower_alert_candidates (user_id, dedupe_key);

-- Per-user chronological lookup (most recent first)
CREATE INDEX IF NOT EXISTS idx_alert_candidates_user_created
    ON public.watchtower_alert_candidates (user_id, created_at DESC);

-- Per-user/ticker lookup
CREATE INDEX IF NOT EXISTS idx_alert_candidates_user_ticker
    ON public.watchtower_alert_candidates (user_id, ticker, created_at DESC);

-- Per-user/status lookup
CREATE INDEX IF NOT EXISTS idx_alert_candidates_user_status
    ON public.watchtower_alert_candidates (user_id, status, created_at DESC);

-- RLS: authenticated users can read their own rows only.
-- The service role bypasses RLS for internal policy writes.
ALTER TABLE public.watchtower_alert_candidates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS alert_candidates_user_policy ON public.watchtower_alert_candidates;
CREATE POLICY alert_candidates_user_policy
    ON public.watchtower_alert_candidates FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- ── ROLLBACK (commented out by default) ──────────────────────────────────────
-- DROP TABLE IF EXISTS public.watchtower_alert_candidates CASCADE;
