-- Phase 4 — portfolio synthesis persistence.
--
-- Adds a JSONB column to agent_runs so the dedicated portfolio-level
-- synthesis output (portfolio_bias, key_themes, risk_concentrations,
-- overexposure_flags, rebalancing_suggestions) is queryable without
-- re-parsing the free-text ``summary`` column.
--
-- Idempotent — safe to run multiple times.

alter table public.agent_runs
    add column if not exists portfolio_synthesis jsonb;

alter table public.agent_runs
    add column if not exists synthesis_used_fallback boolean not null default false;

create index if not exists agent_runs_synthesis_bias_idx
    on public.agent_runs ((portfolio_synthesis ->> 'portfolio_bias'));
