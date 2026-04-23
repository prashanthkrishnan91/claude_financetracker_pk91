-- Phase 5 — cost + failure control.
--
-- Adds run-mode classification and cost metrics to agent_runs so the UI
-- can answer "how much did this run cost?" and show the DEGRADED badge
-- with its trigger reason.
--
-- Idempotent — safe to run multiple times.

alter table public.agent_runs
    add column if not exists run_mode text not null default 'FULL';

alter table public.agent_runs
    add column if not exists run_mode_decision jsonb;

alter table public.agent_runs
    add column if not exists cost_metrics jsonb;

-- Constraint the enum values at the DB level so a future refactor
-- can't silently insert an unknown mode.
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'agent_runs_run_mode_chk'
    ) then
        alter table public.agent_runs
            add constraint agent_runs_run_mode_chk
            check (run_mode in ('FULL', 'DEGRADED'));
    end if;
end$$;

create index if not exists agent_runs_run_mode_idx
    on public.agent_runs (run_mode);
