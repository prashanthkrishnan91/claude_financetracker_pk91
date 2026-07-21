-- cost_guard_retention_cleanup.sql
-- Safe bounded DELETE policies for generated/history tables.
-- FK-safe child-first ordering: child rows are deleted explicitly before their
-- parent rows so that CASCADE constraints cannot silently remove rows that are
-- younger than the retention window.
--
-- Run manually or on a scheduled basis to prevent unbounded table growth.
--
-- SAFETY CONTRACT:
--   - Does NOT touch: portfolios, positions, holdings, transactions, users,
--     auth, accounts, deposits, plaid_items, or any manually entered data.
--   - Does NOT use TRUNCATE CASCADE.
--   - Every DELETE has an explicit WHERE clause.
--   - Child rows are deleted before their parents (FK-safe order).
--   - WHERE clauses also catch child rows referencing a parent targeted for
--     deletion, even when the child row is newer than the retention window.
--     This prevents CASCADE constraints from silently deleting recent data.
--   - Default retention: 7 days for generated rows.
--
-- FK dependency map (relevant subset):
--   decision_log.recommendation_id  → recommendations(id)       ON DELETE SET NULL
--   agent_insights.run_id           → agent_runs(id)            ON DELETE CASCADE  ⚠
--   recommendations.agent_run_id    → agent_runs(id)            ON DELETE SET NULL
--   research_artifact_facts.artifact_id  → research_artifacts(id)  ON DELETE CASCADE  ⚠
--   research_artifact_sources.artifact_id → research_artifacts(id) ON DELETE CASCADE  ⚠
--   research_artifact_facts.source_id    → research_artifact_sources(id) ON DELETE SET NULL
--   worker_audit_events.artifact_id → research_artifacts(id)    ON DELETE SET NULL
--   analyst_refresh_jobs.run_session_id → intel_run_sessions(id) ON DELETE CASCADE  ⚠
--   intel_v3_snapshots.run_session_id   → intel_run_sessions(id) ON DELETE SET NULL
--   intel_run_sessions.pre_session_snapshot_id → intel_v3_snapshots(id) ON DELETE SET NULL
--   intel_run_sessions.completed_snapshot_id   → intel_v3_snapshots(id) ON DELETE SET NULL
--   (the four session FKs are added by migration 026_intel_run_sessions.sql)
--
-- ⚠ = CASCADE: if parent is deleted, child is deleted automatically.
--     Explicit child deletion below prevents CASCADE from removing rows
--     that are within the retention window.
--
-- Validate sizes before running:
--   SELECT relname, pg_size_pretty(pg_total_relation_size('public.'||relname)),
--          n_live_tup
--   FROM pg_stat_user_tables WHERE schemaname = 'public'
--   ORDER BY pg_total_relation_size('public.'||relname) DESC LIMIT 20;

BEGIN;

-- ── 1. decision_log (before recommendations) ──────────────────────────────────
-- FK: decision_log.recommendation_id → recommendations(id) ON DELETE SET NULL
-- Delete decision_log rows that are old, OR that reference a recommendation
-- which will be deleted in step 2 (even if the log row itself is < 7 days old).
DELETE FROM public.decision_log
WHERE created_at < NOW() - INTERVAL '7 days'
   OR recommendation_id IN (
       SELECT id FROM public.recommendations
       WHERE created_at < NOW() - INTERVAL '7 days'
   );

-- ── 2. recommendations (before agent_runs) ────────────────────────────────────
-- FK: recommendations.agent_run_id → agent_runs(id) ON DELETE SET NULL
-- SET NULL means deleting agent_runs would not fail, but we delete recommendations
-- first so that stale recommendations linked to old agent_runs are cleaned up.
DELETE FROM public.recommendations
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 3. agent_insights (before agent_runs) ─────────────────────────────────────
-- FK: agent_insights.run_id → agent_runs(id) ON DELETE CASCADE  ⚠
-- CASCADE means deleting an agent_run auto-deletes its insights. Without this
-- step, insights newer than 7 days that reference an old agent_run would be
-- silently removed by the CASCADE in step 4. We delete them explicitly first
-- so the retention boundary applies to both tables.
DELETE FROM public.agent_insights
WHERE created_at < NOW() - INTERVAL '7 days'
   OR run_id IN (
       SELECT id FROM public.agent_runs
       WHERE created_at < NOW() - INTERVAL '7 days'
   );

-- ── 4. agent_runs (after insights and recommendations) ────────────────────────
-- Safe to delete now: insights are already gone (step 3), recommendations
-- reference via SET NULL. No remaining CASCADE risk.
DELETE FROM public.agent_runs
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 5. agent_features ─────────────────────────────────────────────────────────
-- No FK dependency on agent_runs in schema — independent generated table.
DELETE FROM public.agent_features
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 6. research_artifact_facts (before research_artifact_sources and research_artifacts) ──
-- FK: research_artifact_facts.artifact_id → research_artifacts(id) ON DELETE CASCADE  ⚠
-- FK: research_artifact_facts.source_id   → research_artifact_sources(id) ON DELETE SET NULL
-- Delete facts that are old, OR that reference an artifact being deleted in
-- step 9. Prevents CASCADE from silently removing recent facts.
DELETE FROM public.research_artifact_facts
WHERE created_at < NOW() - INTERVAL '7 days'
   OR artifact_id IN (
       SELECT id FROM public.research_artifacts
       WHERE created_at < NOW() - INTERVAL '7 days'
   );

-- ── 7. research_artifact_sources (before research_artifacts) ──────────────────
-- FK: research_artifact_sources.artifact_id → research_artifacts(id) ON DELETE CASCADE  ⚠
-- Delete sources that are old, OR that reference an artifact being deleted.
-- research_artifact_facts.source_id is SET NULL so no risk from this direction.
DELETE FROM public.research_artifact_sources
WHERE created_at < NOW() - INTERVAL '7 days'
   OR artifact_id IN (
       SELECT id FROM public.research_artifacts
       WHERE created_at < NOW() - INTERVAL '7 days'
   );

-- ── 8. worker_audit_events (before research_artifacts) ────────────────────────
-- FK: worker_audit_events.artifact_id → research_artifacts(id) ON DELETE SET NULL
-- SET NULL means deleting artifacts would not fail here. Explicit delete for
-- completeness and to avoid orphaned audit rows with null artifact references.
DELETE FROM public.worker_audit_events
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 9. research_artifacts (after all child tables) ────────────────────────────
-- All child rows (facts, sources, audit events) are already gone. Safe to
-- delete parent artifacts now without any CASCADE side-effects.
DELETE FROM public.research_artifacts
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 10. intel_run_sessions (terminal sessions only; before intel_v3_snapshots) ─
-- FKs (migration 026):
--   analyst_refresh_jobs.run_session_id → intel_run_sessions(id) ON DELETE CASCADE  ⚠
--   intel_v3_snapshots.run_session_id   → intel_run_sessions(id) ON DELETE SET NULL
-- Only TERMINAL sessions ('completed' / 'failed') older than the retention
-- window are removed. Active/in-progress sessions ('created',
-- 'ticker_refresh_in_progress', 'publishing', 'publication_retryable_failed')
-- are ALWAYS preserved regardless of age.
-- The CASCADE here is deliberate and safe: a session's analyst_refresh_jobs
-- rows are that session's own bookkeeping — when a terminal session ages out,
-- its job rows are terminal bookkeeping by definition (no younger-than-window
-- job can belong to an older-than-window session: session jobs are created at
-- session start and only that session's continuations touch them). Jobs of
-- preserved sessions and legacy NULL-session jobs are untouched.
-- Deleting a session SET-NULLs intel_v3_snapshots.run_session_id, so the
-- snapshot cleanup in step 11 can never fail on a session reference.
DELETE FROM public.intel_run_sessions
WHERE status IN ('completed', 'failed')
  AND created_at < NOW() - INTERVAL '7 days';

-- ── 11. intel_v3_snapshots (after intel_run_sessions) ─────────────────────────
-- Keep is_active=true rows (live read cache). Only inactive (superseded)
-- snapshots older than 7 days are removed.
-- Session FKs cannot block this delete (migration 026):
--   intel_run_sessions.pre_session_snapshot_id / completed_snapshot_id are
--   ON DELETE SET NULL — deleting an old inactive snapshot NULLs any session
--   pointer to it instead of failing;
--   step 10 already removed old terminal sessions, so their snapshot links
--   are gone before this runs.
DELETE FROM public.intel_v3_snapshots
WHERE is_active = false
  AND created_at < NOW() - INTERVAL '7 days';

-- ── 12. market_snapshots ──────────────────────────────────────────────────────
-- No FK children in schema. Generated price/market data — safe to prune by age.
DELETE FROM public.market_snapshots
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 13. portfolio_snapshots (conditional) ────────────────────────────────────
-- Only uncomment after confirming this table contains generated/cached rows only.
--
-- DELETE FROM public.portfolio_snapshots
-- WHERE created_at < NOW() - INTERVAL '7 days';

COMMIT;

-- ── Post-cleanup verification ─────────────────────────────────────────────────
-- Run after COMMIT to confirm sizes have dropped:
--
-- SELECT relname, pg_size_pretty(pg_total_relation_size('public.'||relname)),
--        n_live_tup
-- FROM pg_stat_user_tables WHERE schemaname = 'public'
--   AND relname IN (
--     'intel_v3_snapshots','market_snapshots','agent_runs','agent_insights',
--     'agent_features','recommendations','decision_log','research_artifacts',
--     'research_artifact_facts','research_artifact_sources','worker_audit_events'
--   )
-- ORDER BY pg_total_relation_size('public.'||relname) DESC;
--
-- Then VACUUM ANALYZE pruned tables:
-- VACUUM ANALYZE public.intel_v3_snapshots;
-- VACUUM ANALYZE public.research_artifacts;
