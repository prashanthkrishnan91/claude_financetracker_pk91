-- ============================================================================
-- Watchlist — user-defined candidate tickers with user-defined criteria
-- ============================================================================
-- Additive migration — does NOT modify any existing table.
-- Safe to re-run: all statements use IF NOT EXISTS guards.
--
-- WHY: the lean product's third view. The user defines tickers and simple
-- deterministic criteria; the app flags entries whose criteria are met.
-- The app surfaces candidates only — it never picks stocks.
--
-- MANUAL ACTION REQUIRED (Supabase) — this migration is NOT auto-applied:
--   1. Open the Supabase production project → SQL Editor → New query.
--   2. Paste the contents of this file and Run.
--   3. Verify: SELECT COUNT(*) FROM public.watchlist_items;  (0 rows is OK)
--   Until applied, /api/v1/watchlist endpoints will return 500.
--   No other app behavior is affected.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.watchlist_items (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL,
    ticker         TEXT NOT NULL,

    -- User-defined criterion. Deterministic price comparisons only.
    -- Valid values: price_below | price_above
    criteria_type  TEXT NOT NULL CHECK (criteria_type IN ('price_below', 'price_above')),
    threshold      NUMERIC NOT NULL,

    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT watchlist_items_unique_criterion
        UNIQUE (user_id, ticker, criteria_type, threshold)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_items_user
    ON public.watchlist_items (user_id);

ALTER TABLE public.watchlist_items ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'watchlist_items' AND policyname = 'watchlist_items_owner'
    ) THEN
        CREATE POLICY watchlist_items_owner ON public.watchlist_items
            FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;
