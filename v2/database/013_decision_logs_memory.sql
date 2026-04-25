-- Decision Log Memory table for Deploy recommendation snapshots + actual execution edits.
-- Run in Supabase SQL editor.

create table if not exists public.decision_logs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    source text not null default 'deploy',
    status text not null default 'draft' check (status in ('draft', 'executed', 'partially_executed', 'skipped')),
    recommendation_snapshot jsonb not null,
    actual_decisions jsonb not null default '[]'::jsonb,
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_decision_logs_user_created
  on public.decision_logs (user_id, created_at desc);

create index if not exists idx_decision_logs_status
  on public.decision_logs (user_id, status);

create trigger trg_decision_logs_updated_at
before update on public.decision_logs
for each row execute function public.update_updated_at();

-- RLS-compatible defaults (kept disabled by default, matching project auth model).
alter table public.decision_logs disable row level security;

grant all on table public.decision_logs to authenticated;
grant all on table public.decision_logs to service_role;
