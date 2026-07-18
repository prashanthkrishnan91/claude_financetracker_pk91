-- ============================================================================
-- 025 — Watchlist: user-defined tickers with deterministic price criteria
-- ============================================================================
-- ADDITIVE migration — creates one new table and touches nothing else.
-- Idempotent: all statements use IF NOT EXISTS / conditional guards, safe to
-- re-run. No destructive statements (no DROP/TRUNCATE/DELETE/ALTER of any
-- existing object).
--
-- WHY: third primary view of the consolidated product. The user defines
-- tickers and simple deterministic price criteria; the app flags entries
-- whose criteria are met at read time. The app never auto-selects watchlist
-- stocks, and watchlist tickers never enter the Paycheck Advisor candidate
-- set. No alert workers or notifications exist for this table.
--
-- MANUAL ACTION REQUIRED (Supabase; this migration is NOT auto-applied):
--   1. Supabase production project → SQL Editor → New query.
--   2. Paste this file's contents and Run.
--   3. Validate:
--        SELECT COUNT(*) FROM public.watchlist_items;              -- 0 rows is OK
--        SELECT relrowsecurity FROM pg_class
--          WHERE oid = 'public.watchlist_items'::regclass;         -- must be true
--        SELECT policyname FROM pg_policies
--          WHERE tablename = 'watchlist_items';                    -- watchlist_items_owner
--   Until applied, /api/v1/watchlist endpoints return 503
--   watchlist_migration_required (a deliberate operational state, not a bug).
--   No other app behavior depends on this table.
--
-- DUPLICATE POLICY: one row per (user, ticker, criterion direction). The same
-- ticker may have both a price_below and a price_above entry; a second entry
-- with the same direction is rejected (API returns 409; DB constraint is the
-- backstop). Threshold changes are edits (PATCH), not new rows.
--
-- ROLLBACK: reverting the application PR leaves this table unused and
-- harmless — historical rows are preserved and nothing references them.
-- Optional manual teardown (NOT part of this PR; destructive, run only if
-- you explicitly want the data gone):
--   -- DROP TABLE IF EXISTS public.watchlist_items;
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.watchlist_items (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL,
    ticker         TEXT NOT NULL
        CHECK (ticker = upper(ticker) AND length(ticker) BETWEEN 1 AND 17),

    -- Deterministic price comparisons only.
    criteria_type  TEXT NOT NULL CHECK (criteria_type IN ('price_below', 'price_above')),
    threshold      NUMERIC NOT NULL CHECK (threshold > 0),

    notes          TEXT CHECK (notes IS NULL OR length(notes) <= 500),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ,

    CONSTRAINT watchlist_items_one_per_direction
        UNIQUE (user_id, ticker, criteria_type)
);

-- User-scoped reads are the only access pattern.
CREATE INDEX IF NOT EXISTS idx_watchlist_items_user
    ON public.watchlist_items (user_id, created_at);

ALTER TABLE public.watchlist_items ENABLE ROW LEVEL SECURITY;

-- Owner-only policy: a user can read/insert/update/delete only their rows.
-- (The backend uses the service-role client with explicit user_id scoping;
-- RLS is the defense-in-depth backstop for any anon/user-key access path.)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'watchlist_items'
          AND policyname = 'watchlist_items_owner'
    ) THEN
        CREATE POLICY watchlist_items_owner ON public.watchlist_items
            FOR ALL
            USING (auth.uid() = user_id)
            WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;
