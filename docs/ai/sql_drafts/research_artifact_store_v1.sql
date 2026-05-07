-- ============================================================================
-- DRAFT ONLY — DO NOT APPLY TO PRODUCTION UNTIL APPROVED
-- ----------------------------------------------------------------------------
-- This file lives in `docs/ai/sql_drafts/`, NOT in `v2/database/`, on purpose.
-- Promoting this draft requires an explicit follow-on PR that:
--   1. Re-reviews against `docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md`
--      and `docs/ai/INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md`.
--   2. Resolves the open questions in §12 of the spec doc.
--   3. Renames this file to `v2/database/017_research_artifact_store_v1.sql`.
--   4. Applies it via the Supabase SQL Editor in a maintenance window.
--   5. Updates HANDOFF.md and progress_log.md with "Supabase SQL: Yes".
-- ============================================================================
-- Intel v3 — Research Artifact Store v1
-- ============================================================================
-- Purpose: durable, typed, RLS-aware substrate for SOURCED RESEARCH ARTIFACTS
-- produced by future research workers (Phase 3+). Forbidden visible-decision
-- fields (final_action, buy/hold/trim/sell, final_conviction, final_allocation,
-- deploy_amount, etc.) are HARD-rejected at write time. This store does NOT
-- determine visible Buy/Hold/Trim/Sell — that authority remains with
-- `decide()` in `v2/backend/app/services/intelligence/v3/decision_policy_v1.py`.
--
-- Design fit: see `docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md` (Phase 2 spec).
-- Binding contracts: see `docs/ai/INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md`
--   §1, §4, §5, §7, §8.
--
-- Style: matches `v2/database/016_intel_v3_snapshots.sql` — additive,
-- idempotent (`IF NOT EXISTS` and `DO $$` guards), RLS-gated, owner-only.
-- ============================================================================

-- Required extensions (already enabled by 001_initial_schema.sql in production;
-- listed here for portability).
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ============================================================================
-- 1. RESEARCH_ARTIFACTS — parent record per worker artifact
-- ============================================================================
-- One row per artifact produced by a research worker. Carries governance,
-- provenance, freshness, idempotency, deterministic-consumption gating, and a
-- sanitized plain-English summary. Forbidden visible-decision fields are
-- rejected at write time via:
--   (a) bounded CHECKs on artifact_type / scope_kind / confidence / freshness,
--   (b) column-level CHECK on `payload` JSONB top-level keys,
--   (c) BEFORE INSERT/UPDATE trigger that scans nested `payload` recursively.

CREATE TABLE IF NOT EXISTS public.research_artifacts (
    id                                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                           UUID NOT NULL,

    -- Versioning / typing
    artifact_schema_version           TEXT NOT NULL DEFAULT 'artifact.v1',
    artifact_type                     TEXT NOT NULL
        CHECK (artifact_type IN (
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
            'thesis_update'
        )),
    skill_pack                        TEXT NOT NULL,

    -- Scope
    scope_kind                        TEXT NOT NULL
        CHECK (scope_kind IN ('ticker', 'portfolio', 'watchlist_candidate')),
    ticker                            TEXT,
    -- ticker MUST be present when scope_kind is 'ticker' or 'watchlist_candidate'
    CONSTRAINT research_artifacts_ticker_scope_chk
        CHECK (
            (scope_kind = 'portfolio' AND ticker IS NULL)
            OR (scope_kind IN ('ticker', 'watchlist_candidate') AND ticker IS NOT NULL)
        ),

    -- Lifecycle / provenance
    generated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_window_start               TIMESTAMPTZ,
    source_window_end                 TIMESTAMPTZ,
    generated_by_worker               TEXT NOT NULL,
    generated_by_model                TEXT,
    model_version                     TEXT,
    input_fingerprint                 TEXT NOT NULL,
    replay_idempotency_key            TEXT NOT NULL,
    parent_intel_run_id               UUID,
    worker_run_id                     UUID NOT NULL,

    -- Trust / freshness
    confidence_or_trust_level         TEXT NOT NULL DEFAULT 'LOW'
        CHECK (confidence_or_trust_level IN ('HIGH', 'MEDIUM', 'LOW', 'UNKNOWN')),
    freshness_status                  TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (freshness_status IN ('FRESH', 'STALE', 'UNKNOWN')),
    expires_at                        TIMESTAMPTZ,
    invalidated_at                    TIMESTAMPTZ,
    invalidation_reason               TEXT,
    is_active                         BOOLEAN NOT NULL DEFAULT TRUE,

    -- Deterministic-consumption gate (workers MUST NOT set this; only future
    -- truth adapter may flip to TRUE — see spec §6).
    safe_for_decision                 BOOLEAN NOT NULL DEFAULT FALSE,
    deterministic_inputs_allowed      TEXT[] NOT NULL DEFAULT '{}',
    deterministic_inputs_forbidden    TEXT[] NOT NULL DEFAULT '{}',

    -- Visible / advisory text and gaps
    evidence_summary_plain_english    TEXT,
    limitations_or_missing_evidence   TEXT[] NOT NULL DEFAULT '{}',

    -- Typed payload (kind-specific). Hard-rejects forbidden visible-decision
    -- top-level keys; trigger below scans nested keys recursively.
    payload                           JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT research_artifacts_payload_no_forbidden_keys_chk
        CHECK (
            NOT (payload ?| ARRAY[
                'final_action',
                'final_conviction',
                'final_allocation',
                'deploy_amount',
                'deploy_dollar',
                'deploy_shares',
                'buy',
                'sell',
                'trim',
                'hold'
            ])
        ),

    created_at                        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Comments on critical fields.
COMMENT ON TABLE  public.research_artifacts IS
    'Phase 2 Research Artifact Store v1. Sourced research artifacts only. Never sets visible Buy/Hold/Trim/Sell. See docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md.';
COMMENT ON COLUMN public.research_artifacts.user_id IS
    'Owner. RLS scope. Matches intel_v3_snapshots.user_id pattern (no FK on this v3 tier).';
COMMENT ON COLUMN public.research_artifacts.artifact_type IS
    'Bounded enum aligned with the 11+1 skill packs in INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md §3.';
COMMENT ON COLUMN public.research_artifacts.replay_idempotency_key IS
    'Deterministic hash over (skill_pack, scope_kind, ticker_or_scope, source_refs_fingerprint, model_version). UNIQUE per active row to collapse replays.';
COMMENT ON COLUMN public.research_artifacts.confidence_or_trust_level IS
    'Artifact trust, NOT decision conviction. See spec §6 / Phase 1 §8 risk #12.';
COMMENT ON COLUMN public.research_artifacts.safe_for_decision IS
    'Defaults FALSE. Workers MUST NOT set this. Only the future truth adapter (Phase 4/5) may flip to TRUE behind a per-axis flag.';
COMMENT ON COLUMN public.research_artifacts.deterministic_inputs_allowed IS
    'Explicit axis allow-list (e.g. {evidence_axis_band, risk_axis_band}). Anything outside is ignored by the future truth adapter.';
COMMENT ON COLUMN public.research_artifacts.payload IS
    'Typed payload. Forbidden visible-decision keys are rejected by CHECK constraint and a recursive BEFORE trigger.';

-- Idempotency: one active row per replay_idempotency_key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_research_artifacts_replay_active
    ON public.research_artifacts (replay_idempotency_key)
    WHERE is_active = TRUE;

-- Hot-path indexes.
CREATE INDEX IF NOT EXISTS idx_research_artifacts_user_ticker_active
    ON public.research_artifacts (user_id, ticker, is_active, generated_at DESC)
    WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_research_artifacts_user_type_active
    ON public.research_artifacts (user_id, artifact_type, is_active, generated_at DESC)
    WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_research_artifacts_user_freshness
    ON public.research_artifacts (user_id, freshness_status, expires_at)
    WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_research_artifacts_parent_intel_run
    ON public.research_artifacts (parent_intel_run_id)
    WHERE parent_intel_run_id IS NOT NULL;


-- ============================================================================
-- 2. RESEARCH_ARTIFACT_SOURCES — citations / provenance per artifact
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.research_artifact_sources (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id              UUID NOT NULL REFERENCES public.research_artifacts(id) ON DELETE CASCADE,
    user_id                  UUID NOT NULL,  -- denormalized for RLS

    source_kind              TEXT NOT NULL
        CHECK (source_kind IN (
            'sec_filing',
            'transcript',
            'vendor_calendar',
            'news',
            'vendor_fundamentals',
            'vendor_estimates',
            'peer_set_def',
            'press_release',
            'company_disclosure',
            'other'
        )),
    provider_name            TEXT NOT NULL,
    provider_version         TEXT,
    source_url               TEXT,
    source_id                TEXT,        -- e.g. SEC accession number
    source_published_at      TIMESTAMPTZ,
    fetched_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    quote_or_excerpt         TEXT,
    section_reference        TEXT,
    source_hash              TEXT,        -- for dedup

    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.research_artifact_sources IS
    'Provenance/citations for research_artifacts rows. At least one required for any artifact whose facts make external claims.';
COMMENT ON COLUMN public.research_artifact_sources.user_id IS
    'Denormalized from research_artifacts.user_id for simpler RLS. Trigger asserts equality.';
COMMENT ON COLUMN public.research_artifact_sources.source_kind IS
    'Bounded enum. Free news is NOT authoritative — see spec §6 / Phase 1 §3.12.';

CREATE INDEX IF NOT EXISTS idx_research_artifact_sources_artifact
    ON public.research_artifact_sources (artifact_id);
CREATE INDEX IF NOT EXISTS idx_research_artifact_sources_user_kind_pubd
    ON public.research_artifact_sources (user_id, source_kind, source_published_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_artifact_sources_source_id
    ON public.research_artifact_sources (source_id)
    WHERE source_id IS NOT NULL;


-- ============================================================================
-- 3. RESEARCH_ARTIFACT_FACTS — typed structured observations
-- ============================================================================
-- Replaces the addendum's separate sourced_claim / metric_observation /
-- risk_item / catalyst_item / thesis_pillar tables via a `fact_kind`
-- discriminator + typed `structured_payload`.

CREATE TABLE IF NOT EXISTS public.research_artifact_facts (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id              UUID NOT NULL REFERENCES public.research_artifacts(id) ON DELETE CASCADE,
    user_id                  UUID NOT NULL,  -- denormalized for RLS
    source_id                UUID REFERENCES public.research_artifact_sources(id) ON DELETE SET NULL,

    fact_kind                TEXT NOT NULL
        CHECK (fact_kind IN (
            'metric_observation',
            'risk_item',
            'catalyst_item',
            'thesis_pillar',
            'sourced_claim',
            'event',
            'peer_context',
            'quality_observation',
            'revision_note'
        )),
    axis_hint                TEXT
        CHECK (axis_hint IS NULL OR axis_hint IN (
            'evidence', 'risk', 'price', 'quality', 'catalyst', 'exposure'
        )),
    severity                 TEXT
        CHECK (severity IS NULL OR severity IN ('LOW', 'MEDIUM', 'HIGH', 'UNKNOWN')),
    period                   TEXT,             -- e.g. '2026Q1'
    as_of                    TIMESTAMPTZ,
    is_quote_grounded        BOOLEAN NOT NULL DEFAULT FALSE,

    -- Typed kind-specific structure. Same forbidden-key rule as parent payload.
    structured_payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT research_artifact_facts_payload_no_forbidden_keys_chk
        CHECK (
            NOT (structured_payload ?| ARRAY[
                'final_action',
                'final_conviction',
                'final_allocation',
                'deploy_amount',
                'deploy_dollar',
                'deploy_shares',
                'buy',
                'sell',
                'trim',
                'hold'
            ])
        ),

    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.research_artifact_facts IS
    'Typed structured observations under a parent artifact. fact_kind discriminator covers the addendum''s sourced_claim / metric_observation / risk_item / catalyst_item / thesis_pillar.';
COMMENT ON COLUMN public.research_artifact_facts.axis_hint IS
    'Advisory hint for which decision axis this fact MAY influence. Binding only when the parent artifact lists it in deterministic_inputs_allowed and the per-axis flag is enabled (Phase 5).';
COMMENT ON COLUMN public.research_artifact_facts.is_quote_grounded IS
    'TRUE when the fact is backed by a quoted source excerpt (research_artifact_sources.quote_or_excerpt). HIGH confidence requires at least one quote-grounded fact per spec §6.';

CREATE INDEX IF NOT EXISTS idx_research_artifact_facts_artifact_kind
    ON public.research_artifact_facts (artifact_id, fact_kind);
CREATE INDEX IF NOT EXISTS idx_research_artifact_facts_user_kind_asof
    ON public.research_artifact_facts (user_id, fact_kind, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_research_artifact_facts_source
    ON public.research_artifact_facts (source_id)
    WHERE source_id IS NOT NULL;


-- ============================================================================
-- 4. WORKER_AUDIT_EVENTS — audit trail per worker tool call
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.worker_audit_events (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id              UUID REFERENCES public.research_artifacts(id) ON DELETE SET NULL,
    user_id                  UUID NOT NULL,
    worker_run_id            UUID NOT NULL,
    parent_intel_run_id      UUID,

    skill_pack               TEXT NOT NULL,
    tool_call                TEXT NOT NULL,    -- e.g. edgar_fetch, transcript_provider, llm_extract
    input_digest             TEXT,
    output_digest            TEXT,
    model_id                 TEXT,
    model_version            TEXT,
    latency_ms               INTEGER,
    cost_estimate_usd        NUMERIC(12,6),

    status                   TEXT NOT NULL
        CHECK (status IN ('completed', 'failed', 'rejected', 'timeout', 'cost_capped')),
    rejected_claims          JSONB,
    error_message            TEXT,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.worker_audit_events IS
    'Per-tool-call audit trail. Required by Phase 1 §5 worker boundary contract: model id, latency, cost, rejected claims, digests for replay.';
COMMENT ON COLUMN public.worker_audit_events.artifact_id IS
    'NULL when the call was rejected before any artifact row was written.';

CREATE INDEX IF NOT EXISTS idx_worker_audit_events_user_run
    ON public.worker_audit_events (user_id, worker_run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_worker_audit_events_user_intel_run
    ON public.worker_audit_events (user_id, parent_intel_run_id, created_at DESC)
    WHERE parent_intel_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_worker_audit_events_artifact
    ON public.worker_audit_events (artifact_id)
    WHERE artifact_id IS NOT NULL;


-- ============================================================================
-- 5. FORBIDDEN-KEY RECURSIVE TRIGGER
-- ============================================================================
-- The column-level CHECK constraints above only inspect TOP-LEVEL JSONB keys.
-- This trigger walks the whole `payload` / `structured_payload` recursively and
-- rejects any forbidden visible-decision key at any nesting depth. Workers
-- cannot smuggle e.g. `{"recommendation": {"final_action": "BUY"}}`.

CREATE OR REPLACE FUNCTION public.research_artifact_reject_forbidden_keys()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $func$
DECLARE
    forbidden_keys TEXT[] := ARRAY[
        'final_action',
        'final_conviction',
        'final_allocation',
        'deploy_amount',
        'deploy_dollar',
        'deploy_shares',
        'buy', 'sell', 'trim', 'hold'
    ];
    target_payload JSONB;
    found_key TEXT;
BEGIN
    -- Identify the payload column based on the trigger's table.
    IF TG_TABLE_NAME = 'research_artifacts' THEN
        target_payload := NEW.payload;
    ELSIF TG_TABLE_NAME = 'research_artifact_facts' THEN
        target_payload := NEW.structured_payload;
    ELSE
        RETURN NEW;
    END IF;

    -- Recursively walk all keys of all object nodes inside the JSONB tree.
    -- `jsonb_path_query` returns a single `jsonb` column (NOT a composite),
    -- so `(kv).key` is invalid SQL. The JSONPath `keyvalue()` accessor emits
    -- objects shaped `{"key":"…","value":…,"id":…}`; we extract the key via
    -- `->> 'key'`. We use `lax` mode (Postgres default) explicitly so that
    -- non-object nodes encountered by the recursive `**` descent are silently
    -- skipped instead of raising. Empty/NULL payloads are coalesced to `{}`.
    -- DRAFT NOTE: this trigger body MUST be syntax-validated against the
    -- target Postgres version (Supabase PG 14/15) before promotion to
    -- `v2/database/`. See `docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md`
    -- §11 (migration/runbook) and §12 (open question on recursive scan
    -- performance / portability).
    FOR found_key IN
        SELECT DISTINCT t.value ->> 'key'
        FROM jsonb_path_query(
                 COALESCE(target_payload, '{}'::jsonb),
                 'lax $.**.keyvalue()'
             ) AS t(value)
    LOOP
        IF found_key IS NOT NULL AND lower(found_key) = ANY (forbidden_keys) THEN
            RAISE EXCEPTION
                'Forbidden visible-decision key "%" present in % payload. See docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md §10 and Phase 1 spec §4 forbidden-field rule.',
                found_key, TG_TABLE_NAME
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;

    -- Defense-in-depth on user_id consistency for child rows.
    IF TG_TABLE_NAME = 'research_artifact_facts' THEN
        IF NEW.user_id IS DISTINCT FROM (
            SELECT user_id FROM public.research_artifacts WHERE id = NEW.artifact_id
        ) THEN
            RAISE EXCEPTION 'research_artifact_facts.user_id must match parent research_artifacts.user_id'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$func$;

DROP TRIGGER IF EXISTS trg_research_artifacts_reject_forbidden_keys ON public.research_artifacts;
CREATE TRIGGER trg_research_artifacts_reject_forbidden_keys
    BEFORE INSERT OR UPDATE ON public.research_artifacts
    FOR EACH ROW
    EXECUTE FUNCTION public.research_artifact_reject_forbidden_keys();

DROP TRIGGER IF EXISTS trg_research_artifact_facts_reject_forbidden_keys ON public.research_artifact_facts;
CREATE TRIGGER trg_research_artifact_facts_reject_forbidden_keys
    BEFORE INSERT OR UPDATE ON public.research_artifact_facts
    FOR EACH ROW
    EXECUTE FUNCTION public.research_artifact_reject_forbidden_keys();

-- Defense-in-depth: research_artifact_sources.user_id must match parent.
CREATE OR REPLACE FUNCTION public.research_artifact_sources_user_consistency()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $func$
BEGIN
    IF NEW.user_id IS DISTINCT FROM (
        SELECT user_id FROM public.research_artifacts WHERE id = NEW.artifact_id
    ) THEN
        RAISE EXCEPTION 'research_artifact_sources.user_id must match parent research_artifacts.user_id'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$func$;

DROP TRIGGER IF EXISTS trg_research_artifact_sources_user_consistency ON public.research_artifact_sources;
CREATE TRIGGER trg_research_artifact_sources_user_consistency
    BEFORE INSERT OR UPDATE ON public.research_artifact_sources
    FOR EACH ROW
    EXECUTE FUNCTION public.research_artifact_sources_user_consistency();


-- ============================================================================
-- 6. UPDATED_AT MAINTENANCE
-- ============================================================================

CREATE OR REPLACE FUNCTION public.research_artifacts_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $func$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$func$;

DROP TRIGGER IF EXISTS trg_research_artifacts_set_updated_at ON public.research_artifacts;
CREATE TRIGGER trg_research_artifacts_set_updated_at
    BEFORE UPDATE ON public.research_artifacts
    FOR EACH ROW
    EXECUTE FUNCTION public.research_artifacts_set_updated_at();


-- ============================================================================
-- 7. ROW LEVEL SECURITY — owner-only, matches intel_v3_snapshots pattern
-- ============================================================================

ALTER TABLE public.research_artifacts          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_artifact_sources   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_artifact_facts     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.worker_audit_events         ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'research_artifacts'
          AND policyname = 'research_artifacts_owner'
    ) THEN
        CREATE POLICY research_artifacts_owner
            ON public.research_artifacts
            FOR ALL
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'research_artifact_sources'
          AND policyname = 'research_artifact_sources_owner'
    ) THEN
        CREATE POLICY research_artifact_sources_owner
            ON public.research_artifact_sources
            FOR ALL
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'research_artifact_facts'
          AND policyname = 'research_artifact_facts_owner'
    ) THEN
        CREATE POLICY research_artifact_facts_owner
            ON public.research_artifact_facts
            FOR ALL
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'worker_audit_events'
          AND policyname = 'worker_audit_events_owner'
    ) THEN
        CREATE POLICY worker_audit_events_owner
            ON public.worker_audit_events
            FOR ALL
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

-- service_role bypasses RLS by Supabase default. Phase 3 workers use service_role.

-- ============================================================================
-- 8. GRANTS — match existing repo style (see 002_agent_insights.sql)
-- ============================================================================

GRANT ALL ON public.research_artifacts        TO authenticated, service_role;
GRANT ALL ON public.research_artifact_sources TO authenticated, service_role;
GRANT ALL ON public.research_artifact_facts   TO authenticated, service_role;
GRANT ALL ON public.worker_audit_events       TO authenticated, service_role;


-- ============================================================================
-- DRAFT ROLLBACK GUIDANCE — UNCOMMENT BEFORE USE, DRAFT ONLY
-- ============================================================================
-- The block below is intentionally commented out. It is rollback guidance only.
-- Drop order respects foreign keys (children first, then parent, then triggers
-- and functions). All four tables are additive — no other repo table depends on
-- them — so dropping is safe in this draft form.
--
-- BEGIN;
-- DROP TRIGGER IF EXISTS trg_research_artifact_sources_user_consistency      ON public.research_artifact_sources;
-- DROP TRIGGER IF EXISTS trg_research_artifact_facts_reject_forbidden_keys   ON public.research_artifact_facts;
-- DROP TRIGGER IF EXISTS trg_research_artifacts_reject_forbidden_keys        ON public.research_artifacts;
-- DROP TRIGGER IF EXISTS trg_research_artifacts_set_updated_at               ON public.research_artifacts;
-- DROP FUNCTION IF EXISTS public.research_artifact_sources_user_consistency();
-- DROP FUNCTION IF EXISTS public.research_artifact_reject_forbidden_keys();
-- DROP FUNCTION IF EXISTS public.research_artifacts_set_updated_at();
-- DROP TABLE   IF EXISTS public.worker_audit_events;
-- DROP TABLE   IF EXISTS public.research_artifact_facts;
-- DROP TABLE   IF EXISTS public.research_artifact_sources;
-- DROP TABLE   IF EXISTS public.research_artifacts;
-- COMMIT;
--
-- After rollback, the visible Intel v3 path (intel_v3_snapshots, decide(), the
-- ReadOnlyEvidenceAdapter, certification detectors, UI cockpit) is unaffected
-- because no existing code reads or writes these tables.
-- ============================================================================
-- END DRAFT ONLY — DO NOT APPLY TO PRODUCTION UNTIL APPROVED
-- ============================================================================
