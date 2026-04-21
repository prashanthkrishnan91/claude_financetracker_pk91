-- ============================================================================
-- Migration 006 — What Changed Analysis
-- ============================================================================
-- Adds `what_changed` to agent_insights and recommendations so the Intel tab
-- can surface a concise bullet-list of how each ticker's analysis shifted
-- relative to the previous agent run.
-- ============================================================================

ALTER TABLE public.agent_insights
    ADD COLUMN IF NOT EXISTS what_changed TEXT;

ALTER TABLE public.recommendations
    ADD COLUMN IF NOT EXISTS what_changed TEXT;
