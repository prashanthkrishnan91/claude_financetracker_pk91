-- Outcome tracking columns for decision_log
-- Run via Supabase SQL Editor.

ALTER TABLE public.decision_log
  ADD COLUMN IF NOT EXISTS current_price  NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS return_pct     NUMERIC(10,4),
  ADD COLUMN IF NOT EXISTS status         TEXT NOT NULL DEFAULT 'active'
                                            CHECK (status IN ('active', 'closed')),
  ADD COLUMN IF NOT EXISTS closed_at      TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_decision_log_user_status
  ON public.decision_log (user_id, status);
