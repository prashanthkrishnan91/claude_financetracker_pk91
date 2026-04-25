-- Decision log intelligence fields, status normalization, and future PnL fields.

-- 1) Remove legacy/lowercase-compatible constraint before status rewrites.
alter table public.decision_logs
  drop constraint if exists decision_logs_status_check;

-- 2) Normalize legacy statuses to canonical uppercase values.
update public.decision_logs
set status = case
  when status is null or btrim(status) = '' then 'DRAFT'
  when upper(btrim(status)) in ('FULLY_EXECUTED', 'FULLY EXECUTED', 'EXECUTED', 'COMPLETE', 'COMPLETED') then 'FULLY_EXECUTED'
  when upper(btrim(status)) in ('PARTIALLY_EXECUTED', 'PARTIAL', 'PARTIALLY EXECUTED', 'PARTIAL_EXECUTED') then 'PARTIALLY_EXECUTED'
  when upper(btrim(status)) in ('SKIPPED', 'SKIP') then 'SKIPPED'
  when upper(btrim(status)) = 'DRAFT' then 'DRAFT'
  else 'DRAFT'
end
where status is distinct from case
  when status is null or btrim(status) = '' then 'DRAFT'
  when upper(btrim(status)) in ('FULLY_EXECUTED', 'FULLY EXECUTED', 'EXECUTED', 'COMPLETE', 'COMPLETED') then 'FULLY_EXECUTED'
  when upper(btrim(status)) in ('PARTIALLY_EXECUTED', 'PARTIAL', 'PARTIALLY EXECUTED', 'PARTIAL_EXECUTED') then 'PARTIALLY_EXECUTED'
  when upper(btrim(status)) in ('SKIPPED', 'SKIP') then 'SKIPPED'
  when upper(btrim(status)) = 'DRAFT' then 'DRAFT'
  else 'DRAFT'
end;

-- 3) Enforce canonical status values. Safe to rerun.
do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'decision_logs_status_check'
      and conrelid = 'public.decision_logs'::regclass
  ) then
    alter table public.decision_logs
      add constraint decision_logs_status_check
      check (status in ('DRAFT', 'FULLY_EXECUTED', 'PARTIALLY_EXECUTED', 'SKIPPED'));
  end if;
end $$;

alter table public.decision_logs
  add column if not exists decision_delta jsonb,
  add column if not exists risk_behavior text,
  add column if not exists style_shift text,
  add column if not exists execution_gap_percent numeric,
  add column if not exists realized_pnl numeric,
  add column if not exists unrealized_pnl numeric,
  add column if not exists review_date timestamptz;
