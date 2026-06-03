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

-- Backfill existing rows from payload JSONB.
-- Runs once; NULL guard means repeated application is safe.
UPDATE intel_v3_snapshots
SET
  snapshot_source = payload->>'snapshot_source',
  payload_generated_at = CASE
    WHEN payload->>'generated_at' IS NOT NULL
     AND payload->>'generated_at' ~ '^\d{4}-\d{2}-\d{2}'
    THEN (payload->>'generated_at')::TIMESTAMPTZ
    ELSE NULL
  END,
  evidence_mapping_version = payload->>'evidence_mapping_version',
  stage7_contract_complete = (
    payload->>'stage7_explanation_contract_version' = 'stage7_explanation_v2'
  ),
  stage8e_contract_complete = (
    payload->>'stage8e_catalyst_explanation_contract_version' = 'stage8e_catalyst_explanation_v1'
  )
WHERE snapshot_source IS NULL;

-- Partial index: fast single-row lookup for the active snapshot per user.
-- The Watchtower reads this path on every 60-second cycle.
CREATE INDEX IF NOT EXISTS idx_intel_v3_snapshots_user_active_created
  ON intel_v3_snapshots (user_id, created_at DESC)
  WHERE is_active = TRUE;
