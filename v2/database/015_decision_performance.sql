-- Decision performance attribution snapshots (Phase 1)
-- Apply before running decision performance tests.

alter table public.decision_logs
  add column if not exists price_snapshot jsonb,
  add column if not exists performance_snapshot jsonb;
