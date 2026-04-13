-- ============================================================================
-- Migration 002 — Multi-Agent Reasoning Engine
-- ============================================================================
-- Adds two tables + extends `recommendations` to support the TradingAgents-
-- inspired multi-agent pipeline (Sentiment / Technical / Fundamental / PM).
--
-- Run via Supabase SQL Editor: paste and click "Run". Idempotent.
-- ============================================================================

-- ============================================================================
-- 1. AGENT_RUNS — Job tracker for async agent pipelines
-- ============================================================================
-- Each POST /recommendations/refresh kicks off one row here. The UI polls
-- /recommendations/jobs/{id} to drive the progress tracker.

CREATE TABLE IF NOT EXISTS public.agent_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

    -- Lifecycle
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','running','completed','failed')),
    current_agent   TEXT,           -- free-text label shown in the UI tracker
    progress_pct    INTEGER DEFAULT 0 CHECK (progress_pct BETWEEN 0 AND 100),

    -- Inputs
    tickers         TEXT[] NOT NULL DEFAULT '{}',
    deposit_amount  NUMERIC(12,2) DEFAULT 0,
    sale_proceeds   NUMERIC(12,2) DEFAULT 0,

    -- Outputs
    allocation      JSONB DEFAULT '{}'::jsonb,   -- { ticker: dollars_to_deploy }
    summary         TEXT,                        -- PM rollup narrative
    error_message   TEXT,

    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_user_started
    ON public.agent_runs(user_id, started_at DESC);

-- ============================================================================
-- 2. AGENT_INSIGHTS — Per-ticker output of the agent pipeline
-- ============================================================================
-- One row per (run_id, ticker). The frontend AgentInsightCard reads from here.

CREATE TABLE IF NOT EXISTS public.agent_insights (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                 UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    run_id                  UUID REFERENCES public.agent_runs(id) ON DELETE CASCADE,
    ticker                  TEXT NOT NULL,

    -- Core agent outputs (per-ticker)
    investment_thesis       TEXT,                        -- Prose thesis from the PM
    sentiment_score         NUMERIC(5,2),                -- -1.00 .. +1.00
    sentiment_label         TEXT,                        -- bullish/neutral/bearish
    technical_signal        TEXT,                        -- BUY/HOLD/SELL/NEUTRAL
    technical_summary       TEXT,
    fundamental_score       NUMERIC(5,2),                -- -1.00 .. +1.00
    fundamental_summary     TEXT,

    -- Aggregate conviction (weighted blend of the three agents, -1.00 .. +1.00)
    conviction_score        NUMERIC(5,2),

    -- Allocation proposed by the PM (dollars from deposit+sale proceeds)
    suggested_allocation    NUMERIC(12,2) DEFAULT 0,
    suggested_action        TEXT CHECK (suggested_action IN ('BUY','SELL','TRIM','HOLD','REVIEW')),

    created_at              TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(run_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_agent_insights_user_ticker
    ON public.agent_insights(user_id, ticker);
CREATE INDEX IF NOT EXISTS idx_agent_insights_run
    ON public.agent_insights(run_id);

-- ============================================================================
-- 3. RECOMMENDATIONS — Extend to link back to the agent run
-- ============================================================================
-- Keeps the existing Buy/Sell/Trim cards but lets the UI pull the full agent
-- thesis/scores on click.

ALTER TABLE public.recommendations
    ADD COLUMN IF NOT EXISTS agent_run_id UUID REFERENCES public.agent_runs(id) ON DELETE SET NULL;
ALTER TABLE public.recommendations
    ADD COLUMN IF NOT EXISTS investment_thesis TEXT;
ALTER TABLE public.recommendations
    ADD COLUMN IF NOT EXISTS sentiment_score NUMERIC(5,2);
ALTER TABLE public.recommendations
    ADD COLUMN IF NOT EXISTS technical_signal TEXT;
ALTER TABLE public.recommendations
    ADD COLUMN IF NOT EXISTS conviction_score NUMERIC(5,2);
ALTER TABLE public.recommendations
    ADD COLUMN IF NOT EXISTS suggested_allocation NUMERIC(12,2);

-- ============================================================================
-- GRANTS
-- ============================================================================
GRANT ALL ON public.agent_runs TO authenticated, service_role;
GRANT ALL ON public.agent_insights TO authenticated, service_role;
