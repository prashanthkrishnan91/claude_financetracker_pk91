-- Phase 3 — per-ticker analyst verdict persistence.
--
-- Adds the raw analyst output to agent_insights so the Phase 4 synthesis
-- stage can read structured per-ticker context (key_drivers, risks,
-- analyst-reported confidence) without re-parsing investment_thesis.
--
-- The legacy ``suggested_action`` CHECK constraint only permits
-- BUY/SELL/TRIM/HOLD/REVIEW, so Phase 3 maps REDUCE→TRIM and
-- INSUFFICIENT_DATA→HOLD on write. The raw analyst ``action`` is
-- preserved in ``analyst_verdict.action`` so downstream readers can
-- recover the original verdict.
--
-- Idempotent — safe to run multiple times.

alter table public.agent_insights
    add column if not exists analyst_verdict jsonb;

alter table public.agent_insights
    add column if not exists analyst_confidence numeric(5, 2);

create index if not exists agent_insights_analyst_action_idx
    on public.agent_insights ((analyst_verdict ->> 'action'));
