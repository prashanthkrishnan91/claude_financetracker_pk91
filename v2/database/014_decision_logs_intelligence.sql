-- Decision log intelligence fields, auto-derived status support, and future PnL fields.

alter table public.decision_logs
  drop constraint if exists decision_logs_status_check;

alter table public.decision_logs
  add constraint decision_logs_status_check
  check (status in ('FULLY_EXECUTED', 'PARTIALLY_EXECUTED', 'SKIPPED'));

alter table public.decision_logs
  add column if not exists decision_delta jsonb,
  add column if not exists risk_behavior text,
  add column if not exists style_shift text,
  add column if not exists execution_gap_percent numeric,
  add column if not exists realized_pnl numeric,
  add column if not exists unrealized_pnl numeric,
  add column if not exists review_date timestamptz;
