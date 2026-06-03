-- Migration 024: Flat metadata columns on intel_v3_snapshots
--
-- Adds denormalized scalar columns so the Watchtower hot path can read
-- snapshot freshness and contract status without fetching the full
-- payload JSONB (50–200 KB). Eliminates ~14 GB/month PostgREST egress
-- caused by the once-per-minute loop reading payload on every cycle.
--
-- New columns:
--   snapshot_source          TEXT          — mirrors payload->>'snapshot_source'
--   payload_generated_at     TIMESTAMPTZ   — mirrors payload->>'generated_at'
--   evidence_mapping_version TEXT          — mirrors payload->>'evidence_mapping_version'
--   stage7_contract_complete BOOLEAN       — TRUE when snapshot satisfies Stage 7 contract
--   stage8e_contract_complete BOOLEAN      — TRUE when snapshot satisfies Stage 8E contract
--
-- All columns are nullable so existing rows remain valid; the INSERT path
-- (intel_v3_service._persist_snapshot) now populates them on every new write.
-- A one-time backfill below synchronises historical rows.

ALTER TABLE intel_v3_snapshots
  ADD COLUMN IF NOT EXISTS snapshot_source           TEXT,
  ADD COLUMN IF NOT EXISTS payload_generated_at      TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS evidence_mapping_version  TEXT,
  ADD COLUMN IF NOT EXISTS stage7_contract_complete  BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS stage8e_contract_complete BOOLEAN DEFAULT FALSE;

-- Backfill scalar metadata columns from payload JSONB.
-- Runs once; NULL guard means repeated application is safe.
--
-- stage7_contract_complete and stage8e_contract_complete are intentionally
-- left at their column DEFAULT (FALSE) for historical rows.
-- Reason: the Python contract checks structural completeness of holdings cards
-- (evidence_explanation present + non-null for stage7; event_summary present
-- for each sec_catalyst_found=true card for stage8e).  Replicating that logic
-- accurately in SQL against arbitrary historical payloads is fragile.  Instead,
-- we let the Watchtower trigger one deterministic republish per user after deploy
-- (no LLM, no new evidence); the new snapshot is written with the correct
-- Python-computed booleans, and subsequent cycles see TRUE and skip republish.
UPDATE intel_v3_snapshots
SET
  snapshot_source      = payload->>'snapshot_source',
  payload_generated_at = CASE
    WHEN payload->>'generated_at' IS NOT NULL
     AND payload->>'generated_at' ~ '^\d{4}-\d{2}-\d{2}'
    THEN (payload->>'generated_at')::TIMESTAMPTZ
    ELSE NULL
  END,
  evidence_mapping_version = payload->>'evidence_mapping_version'
  -- stage7_contract_complete and stage8e_contract_complete remain FALSE (column default)
  -- and are corrected on the first Watchtower republish after deploy.
WHERE snapshot_source IS NULL;

-- Partial index: fast single-row lookup for the active snapshot per user.
-- The Watchtower reads this path on every 60-second cycle.
CREATE INDEX IF NOT EXISTS idx_intel_v3_snapshots_user_active_created
  ON intel_v3_snapshots (user_id, created_at DESC)
  WHERE is_active = TRUE;
