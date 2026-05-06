-- ============================================================================
-- Intel v3 Snapshots — immutable held-position decision snapshots
-- ============================================================================
-- Additive migration — does NOT modify any existing table.
-- Safe to re-run: all statements use IF NOT EXISTS / DO $$ guards.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.intel_v3_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    run_id          UUID,
    schema_version  TEXT NOT NULL DEFAULT 'v3.1',
    payload         JSONB NOT NULL,
    source_hash     TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index: fast lookup of latest active snapshot per user.
CREATE INDEX IF NOT EXISTS idx_intel_v3_snapshots_user_active
    ON public.intel_v3_snapshots (user_id, is_active, created_at DESC);

-- Index: run_id lookup for status polling.
CREATE INDEX IF NOT EXISTS idx_intel_v3_snapshots_run_id
    ON public.intel_v3_snapshots (run_id)
    WHERE run_id IS NOT NULL;

-- Row Level Security: owner-only read/write.
ALTER TABLE public.intel_v3_snapshots ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'intel_v3_snapshots'
          AND policyname = 'intel_v3_snapshots_owner'
    ) THEN
        CREATE POLICY intel_v3_snapshots_owner
            ON public.intel_v3_snapshots
            FOR ALL
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END $$;
