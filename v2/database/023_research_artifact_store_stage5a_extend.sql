-- ============================================================================
-- Stage 5A — Research Artifact Store: artifact_type extension
-- Migration: 023_research_artifact_store_stage5a_extend.sql
-- ============================================================================
-- Extends the artifact_type CHECK constraint on research_artifacts to include
-- four additional types needed for Stage 5A future workers (technicals,
-- sentiment, company strategy, Journal pattern detection).
--
-- PREREQUISITE: Migration 017_research_artifact_store_v1.sql must be applied
-- first. If 017 is not yet applied, apply it before running this file.
--
-- Existing types (017): filing_risk, catalyst_window, valuation_context,
--   fundamental_quality, capital_allocation, risk_red_team, analyst_revisions,
--   news_event, etf_fund_note, portfolio_exposure, hidden_gem_candidate,
--   thesis_update.
--
-- Stage 5A additions:
--   technical_signal   — technical pattern / indicator evidence worker
--   sentiment_event    — news / transcript sentiment aggregation worker
--   company_strategy   — company strategy and capital-allocation narrative worker
--   journal_pattern    — Journal-derived pattern detection worker
--
-- Mapping to Stage 5A task workers:
--   filings → filing_risk          (existing)
--   fundamentals → fundamental_quality   (existing)
--   technicals → technical_signal        (NEW)
--   sentiment → sentiment_event          (NEW)
--   analyst evidence → analyst_revisions (existing)
--   capital allocation → capital_allocation (existing)
--   company strategy → company_strategy  (NEW)
--   Journal pattern detection → journal_pattern (NEW)
--   Radar candidates → hidden_gem_candidate (existing)
--
-- Safe to re-run: the DO block guards against a missing table (017 not applied)
-- and uses IF NOT EXISTS / DROP CONSTRAINT IF EXISTS patterns.
--
-- Architecture rule (unchanged from 017):
--   Agents/workers/LLMs write SOURCED RESEARCH ARTIFACTS only.
--   They NEVER set visible Buy/Hold/Trim/Sell. That authority belongs exclusively
--   to decide() in decision_policy_v1.py.
--   safe_for_decision defaults FALSE; the Phase 2.1 hard-lock CHECK constraint
--   is NOT changed by this migration.
--
-- Manual apply (AFTER merge, after 017 is confirmed applied):
--   1. Confirm 017 is applied:
--        SELECT relname FROM pg_class
--        WHERE relname = 'research_artifacts';
--   2. Open Supabase SQL Editor and run this file.
--   3. Verify new types accepted:
--        INSERT INTO research_artifacts (user_id, artifact_type, skill_pack,
--          scope_kind, ticker, generated_by_worker, input_fingerprint,
--          replay_idempotency_key, worker_run_id, payload)
--        VALUES (auth.uid(), 'technical_signal', 'test_pack', 'ticker', 'AAPL',
--          'test_worker', 'fp_tech_1', 'key_tech_1', gen_random_uuid(), '{}'::jsonb);
--        -- Expected: INSERT 0 1 (or check_violation if 017 not applied).
--   4. Verify old types still accepted:
--        INSERT INTO research_artifacts (user_id, artifact_type, skill_pack,
--          scope_kind, ticker, generated_by_worker, input_fingerprint,
--          replay_idempotency_key, worker_run_id, payload)
--        VALUES (auth.uid(), 'filing_risk', 'test_pack', 'ticker', 'AAPL',
--          'test_worker', 'fp_filing_1', 'key_filing_1', gen_random_uuid(), '{}'::jsonb);
--        -- Expected: INSERT 0 1.
--   5. Verify forbidden type still rejected:
--        INSERT INTO research_artifacts (..., artifact_type='unknown_type', ...)
--        -- Expected: ERROR check_violation.
-- ============================================================================

DO $$
BEGIN
    -- Guard: only proceed if the research_artifacts table exists (017 applied).
    IF NOT EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname = 'public' AND tablename = 'research_artifacts'
    ) THEN
        RAISE EXCEPTION
            'research_artifacts table does not exist. '
            'Apply 017_research_artifact_store_v1.sql first, then re-run this migration.'
            USING ERRCODE = 'undefined_table';
    END IF;
END $$;


-- ============================================================================
-- Drop and recreate the artifact_type CHECK constraint with extended enum.
-- The inline constraint in 017 gets an auto-generated name. We find it by
-- inspecting pg_constraint rather than hard-coding a name.
-- ============================================================================

DO $$
DECLARE
    _conname text;
BEGIN
    -- Find the existing CHECK constraint on artifact_type.
    SELECT c.conname INTO _conname
    FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON t.relnamespace = n.oid
    WHERE n.nspname = 'public'
      AND t.relname = 'research_artifacts'
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%artifact_type%'
      -- Exclude the ticker_scope_chk which also references scope_kind/ticker but
      -- is not the artifact_type constraint.
      AND pg_get_constraintdef(c.oid) LIKE '%filing_risk%';

    IF _conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE public.research_artifacts DROP CONSTRAINT %I', _conname);
    END IF;
END $$;

ALTER TABLE public.research_artifacts
    ADD CONSTRAINT research_artifacts_artifact_type_check
    CHECK (artifact_type IN (
        -- Original Stage 2/3 types (from 017_research_artifact_store_v1.sql)
        'filing_risk',
        'catalyst_window',
        'valuation_context',
        'fundamental_quality',
        'capital_allocation',
        'risk_red_team',
        'analyst_revisions',
        'news_event',
        'etf_fund_note',
        'portfolio_exposure',
        'hidden_gem_candidate',
        'thesis_update',
        -- Stage 5A additions
        'technical_signal',
        'sentiment_event',
        'company_strategy',
        'journal_pattern'
    ));

COMMENT ON COLUMN public.research_artifacts.artifact_type IS
    'Bounded enum aligned with Stage 5A worker types. '
    'Original 12 types from 017; Stage 5A adds technical_signal, sentiment_event, '
    'company_strategy, journal_pattern. See 023_research_artifact_store_stage5a_extend.sql.';

-- ============================================================================
-- END 023_research_artifact_store_stage5a_extend.sql
-- ============================================================================
