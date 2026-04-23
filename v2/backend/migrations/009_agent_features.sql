-- Phase 2 — Agent feature-engine persistence.
--
-- One row per (run_id, ticker). Populated by the feature_engine after
-- MarketSnapshot rows land in market_snapshots. The Phase 3 per-ticker
-- LLM analyst reads from this table as its structured input.
--
-- Idempotent — safe to run multiple times.

create table if not exists public.agent_features (
    id                          uuid primary key default gen_random_uuid(),
    run_id                      uuid not null,
    user_id                     uuid not null,
    ticker                      text not null,
    as_of                       timestamptz not null default now(),

    -- Trend regime
    trend_regime                text not null default 'range',
    sma20                       numeric,
    sma50                       numeric,
    price                       numeric,

    -- Momentum
    momentum_score              numeric not null default 0,
    return_5d                   numeric,
    return_30d                  numeric,

    -- Volatility
    volatility_regime           text not null default 'medium',
    volatility_30d              numeric,

    -- Relative strength
    benchmark_symbol            text not null default 'SPY',
    benchmark_return_30d        numeric,
    relative_strength_30d       numeric,
    relative_strength_label     text not null default 'inline',

    -- Classification
    sector                      text not null default '',
    industry                    text not null default '',
    category                    text not null default 'Other',

    -- Data-quality envelope
    data_quality_score          numeric not null default 0,
    missing_fields              jsonb not null default '[]'::jsonb,

    created_at                  timestamptz not null default now(),

    constraint agent_features_trend_regime_chk
        check (trend_regime in ('uptrend', 'range', 'downtrend')),
    constraint agent_features_vol_regime_chk
        check (volatility_regime in ('low', 'medium', 'high')),
    constraint agent_features_rs_label_chk
        check (relative_strength_label in ('outperforming', 'inline', 'underperforming'))
);

create index if not exists agent_features_run_idx on public.agent_features (run_id);
create index if not exists agent_features_user_ticker_idx on public.agent_features (user_id, ticker, created_at desc);

alter table public.agent_features enable row level security;

drop policy if exists agent_features_select_own on public.agent_features;
create policy agent_features_select_own on public.agent_features
    for select
    using (auth.uid() = user_id);
