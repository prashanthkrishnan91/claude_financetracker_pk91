-- cost_guard_retention_cleanup.sql
-- Safe bounded DELETE policies for generated/history tables.
-- Run manually or on a scheduled basis to prevent unbounded table growth.
--
-- SAFETY CONTRACT:
--   - Does NOT touch: portfolios, positions, holdings, transactions, users,
--     auth, accounts, deposits, plaid_items, or any manually entered data.
--   - Does NOT use TRUNCATE CASCADE.
--   - Prefers age-based pruning. Where reliable grouping columns exist, also
--     keeps the latest 1 row per natural key (user/ticker/run_type/provider).
--   - Default retention: 7 days for generated rows.
--   - All statements are DELETEs with explicit WHERE clauses. Safe to re-run.
--
-- Validate before running:
--   SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename))
--   FROM pg_tables
--   WHERE schemaname = 'public'
--   ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
--   LIMIT 20;

-- ── Table size snapshot (run this first to measure baseline) ─────────────────
-- SELECT
--   relname AS table_name,
--   pg_size_pretty(pg_total_relation_size('public.' || relname)) AS total_size,
--   pg_size_pretty(pg_relation_size('public.' || relname)) AS table_size,
--   n_live_tup AS live_rows
-- FROM pg_stat_user_tables
-- WHERE schemaname = 'public'
-- ORDER BY pg_total_relation_size('public.' || relname) DESC;

BEGIN;

-- ── 1. intel_v3_snapshots ─────────────────────────────────────────────────────
-- Generated: full Intel v3 JSON payload built on every run/watchtower cycle.
-- This was ~497.9 MB before cleanup — the primary storage incident cause.
-- Keep: only is_active=true rows (the current snapshot per user).
-- Delete: all inactive snapshots older than 7 days.
-- Do NOT delete is_active=true rows — they are the live read cache.
DELETE FROM public.intel_v3_snapshots
WHERE is_active = false
  AND created_at < NOW() - INTERVAL '7 days';

-- ── 2. market_snapshots ───────────────────────────────────────────────────────
-- Generated: price/market data snapshots written by the Watchtower price worker.
-- Was ~18.1 MB before cleanup.
-- Retain: most recent 1 row per ticker (by user_id + ticker if columns exist,
-- otherwise by age). Age-based only since grouping keys may vary by schema.
DELETE FROM public.market_snapshots
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 3. agent_runs ─────────────────────────────────────────────────────────────
-- Generated: records of each agent pipeline invocation. Historical log — not
-- referenced by live read paths after run completion.
DELETE FROM public.agent_runs
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 4. agent_insights ─────────────────────────────────────────────────────────
-- Generated: per-ticker insights produced by agent runs. Stale insights are not
-- surfaced to the UI — only the most recent snapshot matters.
DELETE FROM public.agent_insights
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 5. agent_features ─────────────────────────────────────────────────────────
-- Generated: feature vectors extracted during agent runs. Not user-authored.
DELETE FROM public.agent_features
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 6. recommendations ───────────────────────────────────────────────────────
-- Generated: historical recommendation rows produced by earlier pipeline stages.
-- Safe to prune: live recommendation state is owned by intel_v3_snapshots.
DELETE FROM public.recommendations
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 7. decision_log ───────────────────────────────────────────────────────────
-- Generated: append-only log of Intel v3 decision events. Historical only.
-- Keep recent 7 days for debugging; older rows are safe to prune.
DELETE FROM public.decision_log
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 8. research_artifacts ────────────────────────────────────────────────────
-- Generated: evidence artifacts from research workers (earnings_reviewer, SEC,
-- fundamentals lanes). Never user-authored. safe_for_decision=false always.
-- Keep: rows newer than 7 days, plus latest 1 per (user_id, ticker, artifact_type)
-- if those columns exist. Conservative: age-based only to avoid schema assumptions.
DELETE FROM public.research_artifacts
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 9. research_artifact_facts ────────────────────────────────────────────────
-- Generated: individual metric facts linked to research_artifacts.
-- Orphaned facts (artifact deleted) and old facts are safe to prune.
DELETE FROM public.research_artifact_facts
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 10. research_artifact_sources ────────────────────────────────────────────
-- Generated: source provenance records linked to research_artifacts.
-- Same retention policy as parent artifacts.
DELETE FROM public.research_artifact_sources
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 11. worker_audit_events ──────────────────────────────────────────────────
-- Generated: audit trail from worker runs. Historical diagnostic log.
-- Safe to prune: not referenced by live read paths.
DELETE FROM public.worker_audit_events
WHERE created_at < NOW() - INTERVAL '7 days';

-- ── 12. portfolio_snapshots (conditional) ────────────────────────────────────
-- Generated IF this table exists and contains only computed/cached rows.
-- CAUTION: Only run this block if you have confirmed the table contains
-- generated cache rows, not manually entered portfolio state.
-- Uncomment only after verifying the table schema and contents:
--
-- DELETE FROM public.portfolio_snapshots
-- WHERE created_at < NOW() - INTERVAL '7 days';

COMMIT;

-- ── Post-cleanup verification ─────────────────────────────────────────────────
-- Run after COMMIT to confirm sizes have dropped:
--
-- SELECT
--   relname AS table_name,
--   pg_size_pretty(pg_total_relation_size('public.' || relname)) AS total_size,
--   n_live_tup AS live_rows
-- FROM pg_stat_user_tables
-- WHERE schemaname = 'public'
--   AND relname IN (
--     'intel_v3_snapshots', 'market_snapshots', 'agent_runs', 'agent_insights',
--     'agent_features', 'recommendations', 'decision_log', 'research_artifacts',
--     'research_artifact_facts', 'research_artifact_sources', 'worker_audit_events'
--   )
-- ORDER BY pg_total_relation_size('public.' || relname) DESC;
--
-- Then run VACUUM ANALYZE on any table that was heavily pruned:
-- VACUUM ANALYZE public.intel_v3_snapshots;
-- VACUUM ANALYZE public.market_snapshots;
-- VACUUM ANALYZE public.research_artifacts;
