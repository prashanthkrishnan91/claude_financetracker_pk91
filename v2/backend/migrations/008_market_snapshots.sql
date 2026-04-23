-- Phase 1 — Market snapshot persistence.
--
-- One row per (run_id, ticker). Populated by the agent orchestrator after
-- io_layer.fetch_market_bundle returns, before the LLM is invoked. The
-- feature engine (Phase 2) reads from this table instead of recomputing
-- from raw bundles.
--
-- Idempotent — safe to run multiple times. Drop/recreate is intentionally
-- avoided because Phase 2 will append columns, not rewrite the schema.

create table if not exists public.market_snapshots (
    id                  uuid primary key default gen_random_uuid(),
    run_id              uuid not null,
    user_id             uuid not null,
    ticker              text not null,
    as_of               timestamptz not null default now(),
    price               numeric,
    price_source        text not null default 'unavailable',
    return_1d           numeric,
    return_5d           numeric,
    return_30d          numeric,
    volatility_30d      numeric,
    sector              text not null default '',
    industry            text not null default '',
    category            text not null default 'Other',
    sentiment_label     text not null default 'neutral',
    sentiment_score     numeric,
    news_count          integer not null default 0,
    data_quality_score  numeric not null default 0,
    missing_fields      jsonb not null default '[]'::jsonb,
    fallback_chain      jsonb not null default '[]'::jsonb,
    raw                 jsonb not null default '{}'::jsonb,
    created_at          timestamptz not null default now()
);

create index if not exists market_snapshots_run_idx on public.market_snapshots (run_id);
create index if not exists market_snapshots_user_ticker_idx on public.market_snapshots (user_id, ticker, created_at desc);

-- Row-level security: each user only sees their own snapshots. The
-- orchestrator writes via the service role, so write policies aren't
-- needed — only the read policy below.
alter table public.market_snapshots enable row level security;

drop policy if exists market_snapshots_select_own on public.market_snapshots;
create policy market_snapshots_select_own on public.market_snapshots
    for select
    using (auth.uid() = user_id);
