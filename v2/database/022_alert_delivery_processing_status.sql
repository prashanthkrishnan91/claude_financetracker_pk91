-- ============================================================================
-- Stage 3E patch — Claim-before-send safety
-- 022_alert_delivery_processing_status.sql
-- ============================================================================
-- Additive migration. Does NOT modify existing data rows.
-- Migration 021 is already applied; this extends its schema only.
--
-- WHY: The v1 worker sent via Resend while the row was still status='pending'.
-- If mark_sent() failed after a successful send (DB error, worker crash), the
-- row remained pending and would be resent on the next pass — violating the
-- "never send duplicate emails" requirement.
-- Fix: add a 'processing' status so the worker atomically claims pending rows
-- before sending. A crashed/restarted worker sees processing rows and skips
-- them; only after send succeeds does the row advance to 'sent'.
--
-- MANUAL ACTION REQUIRED (Supabase SQL Editor):
--   1. Paste and Run this file.
--   2. Verify constraint: SELECT conname, consrc
--          FROM pg_constraint
--          WHERE conrelid = 'public.alert_delivery_outbox'::regclass
--          AND contype = 'c' AND conname = 'chk_outbox_status';
--      Expected: status IN ('pending','processing','suppressed','sent','failed','cancelled')
--   3. Verify columns: SELECT column_name FROM information_schema.columns
--          WHERE table_name = 'alert_delivery_outbox'
--          AND column_name IN ('processing_started_at','delivery_attempt_count','last_attempt_at');
--      Expected: 3 rows
--   Until applied: claim_for_delivery() will fail because 'processing' is not a
--   valid status value. The worker will fall back to skipping unclaimable rows.
-- ============================================================================

-- Step 1: expand the status check constraint to allow 'processing'.
-- DROP + ADD is safe because the new constraint is a superset of the old one —
-- no existing row violates the new constraint.
ALTER TABLE public.alert_delivery_outbox
    DROP CONSTRAINT IF EXISTS chk_outbox_status;

ALTER TABLE public.alert_delivery_outbox
    ADD CONSTRAINT chk_outbox_status CHECK (
        status IN ('pending', 'processing', 'suppressed', 'sent', 'failed', 'cancelled')
    );

-- Step 2: delivery tracking columns (all nullable/defaulted — no backfill needed).
ALTER TABLE public.alert_delivery_outbox
    ADD COLUMN IF NOT EXISTS processing_started_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS delivery_attempt_count  INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_attempt_at         TIMESTAMPTZ;

-- Partial index: quickly find stale processing rows for future recovery.
-- Not used by the v1 worker but avoids a schema change in a later patch.
CREATE INDEX IF NOT EXISTS idx_alert_delivery_outbox_processing
    ON public.alert_delivery_outbox (processing_started_at)
    WHERE status = 'processing';

-- ── ROLLBACK (commented out by default) ──────────────────────────────────────
-- ALTER TABLE public.alert_delivery_outbox
--     DROP CONSTRAINT IF EXISTS chk_outbox_status;
-- ALTER TABLE public.alert_delivery_outbox
--     ADD CONSTRAINT chk_outbox_status CHECK (
--         status IN ('pending', 'suppressed', 'sent', 'failed', 'cancelled')
--     );
-- ALTER TABLE public.alert_delivery_outbox
--     DROP COLUMN IF EXISTS processing_started_at,
--     DROP COLUMN IF EXISTS delivery_attempt_count,
--     DROP COLUMN IF EXISTS last_attempt_at;
-- DROP INDEX IF EXISTS idx_alert_delivery_outbox_processing;
