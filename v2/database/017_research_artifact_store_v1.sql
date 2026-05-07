-- ============================================================================
-- Intel v3 — Research Artifact Store v1
-- Migration: 017_research_artifact_store_v1.sql
-- ============================================================================
-- Additive migration — creates 4 new tables, functions, triggers, RLS policies,
-- indexes, and grants. Does NOT modify any existing table. Does NOT apply any
-- production SQL until this file is run via the Supabase SQL Editor after merge
-- and explicit approval.
--
-- Safe to re-run: all DDL uses IF NOT EXISTS / CREATE OR REPLACE / DO $$ guards.
--
-- Design references:
--   docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md  — Phase 2 spec (all sections)
--   docs/ai/INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md — §1, §4, §5, §7, §8
--   docs/ai/sql_drafts/research_artifact_store_v1.sql — Phase 2 draft (superseded)
--   v2/database/016_intel_v3_snapshots.sql           — style reference
--
-- Architecture rule (non-negotiable):
--   Agents/workers/LLMs write SOURCED RESEARCH ARTIFACTS only.
--   They NEVER set visible Buy/Hold/Trim/Sell. That authority belongs exclusively
--   to `decide()` in v2/backend/app/services/intelligence/v3/decision_policy_v1.py.
--   `safe_for_decision` defaults FALSE; only a future truth adapter (Phase 4/5) may
--   flip it to TRUE, never a worker.
--
-- Phase 2.1 promotion validation (resolved before promotion):
--   1. Recursive JSON forbidden-key scan originally used jsonb_path_query(..'lax $.**.keyvalue()').
--      HOTFIX applied after migration applied: replaced with PL/pgSQL recursive JSONB walker
--      (research_artifact_find_forbidden_jsonb_key) because keyvalue() fails on scalar
--      descendants (ERROR: jsonpath item method .keyvalue() can only be applied to an object).
--      Sanity check passed: valid payload returns NULL; nested FINAL_ACTION returns FINAL_ACTION.
--   2. Column alias renamed from ambiguous t(value) to kv(obj) for clarity (superseded by hotfix).
--   3. Forbidden-key comparison uses lower(found_key) = ANY(...) — case-insensitive.
--   4. Child user_id consistency: research_artifact_sources (own trigger) and
--      research_artifact_facts (inside shared trigger) are both covered.
--   5. worker_audit_events user_id consistency trigger ADDED for non-null artifact_id
--      rows (missing from Phase 2 draft — now addressed, see §5b below).
--   6. safe_for_decision: defaults FALSE + DB-level CHECK constraint
--      (research_artifacts_safe_for_decision_phase2_chk) hard-locks it FALSE in
--      Phase 2.1. Workers cannot set it TRUE at all. The Phase 4/5 truth-adapter
--      migration must explicitly DROP this constraint before allowing TRUE.
--   7. No forbidden decision columns or payload keys (final_action, buy/sell/trim/hold,
--      final_conviction, final_allocation, deploy_amount, deploy_dollar, deploy_shares).
--   8. No FK coupling to legacy agent_runs, agent_insights, recommendations, or
--      intel_v3_snapshots. parent_intel_run_id is a bare UUID, no FK.
--   9. Indexes support future read paths without over-indexing. Phase 4 may add
--      additional indexes when hot-path patterns are confirmed.
--  10. Migration is additive; no existing tables are modified.
--  11. research_artifact_facts.source_id, when non-null, is DB-verified to belong
--      to the same artifact_id and user_id (trigger §5, facts branch). Cross-artifact
--      citation smuggling is blocked at write time.
--
-- Manual apply (AFTER merge, with explicit approval only):
--   1. Open Supabase SQL Editor.
--   2. Run this file in full.
--   3. Verify four tables exist:
--        SELECT relname FROM pg_class
--        WHERE relname IN ('research_artifacts','research_artifact_sources',
--                          'research_artifact_facts','worker_audit_events');
--   4. Verify RLS enabled:
--        SELECT relname, relrowsecurity FROM pg_class
--        WHERE relname IN ('research_artifacts','research_artifact_sources',
--                          'research_artifact_facts','worker_audit_events');
--   5. Test forbidden-field rejection (top-level):
--        INSERT INTO research_artifacts (user_id, artifact_type, skill_pack,
--          scope_kind, ticker, generated_by_worker, input_fingerprint,
--          replay_idempotency_key, worker_run_id, payload)
--        VALUES (auth.uid(), 'filing_risk', 'test_pack', 'ticker', 'AAPL',
--          'test_worker', 'fp1', 'key1', gen_random_uuid(),
--          '{"final_action": "BUY"}'::jsonb);
--        -- Expected: ERROR with ERRCODE check_violation.
--   6. Test forbidden-field rejection (nested):
--        -- Same but payload = '{"recommendation": {"final_action": "BUY"}}'::jsonb
--        -- Expected: ERROR from the recursive BEFORE trigger.
--   7. Test safe_for_decision hard lock:
--        INSERT INTO research_artifacts (...same minimal columns..., safe_for_decision := TRUE)
--        -- Expected: ERROR check_violation (research_artifacts_safe_for_decision_phase2_chk).
--   8. Test source_id cross-artifact citation rejection:
--        -- Insert a valid fact with a source_id belonging to a different artifact.
--        -- Expected: ERROR check_violation from trigger §5 facts branch.
--   9. Verify Intel v3 UI still loads from intel_v3_snapshots with no behavior change.
--  10. Confirm no page-load LLM calls.
-- ============================================================================

-- Required extension (already enabled by 001_initial_schema.sql in production).
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ============================================================================
-- 1. RESEARCH_ARTIFACTS — parent record per worker artifact
-- ============================================================================
-- One row per artifact produced by a research worker. Carries governance,
-- provenance, freshness, idempotency, deterministic-consumption gating, and a
-- sanitized plain-English summary.
--
-- Forbidden visible-decision fields are rejected at write time via:
--   (a) bounded CHECK on artifact_type / scope_kind / confidence / freshness,
--   (b) column-level CHECK on `payload` JSONB top-level keys (fast fail for the
--       common exact-lowercase case),
--   (c) BEFORE INSERT/UPDATE trigger (§5 below) that recursively scans all
--       nested keys case-insensitively via PL/pgSQL JSONB walker (hotfix).

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
    -- parent_intel_run_id: bare UUID, intentionally NO FK to intel_v3_snapshots
    -- or any other visible-decision table (see Phase 1 §5 worker boundary contract).
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

    -- Deterministic-consumption gate.
    -- Workers MUST NOT set safe_for_decision to TRUE. Only the future Phase 4/5
    -- truth adapter may flip this flag, and only after the artifact passes the
    -- per-axis evidence contract. This column NEVER feeds into decide() directly.
    --
    -- Phase 2.1 DB-level hard lock: no truth adapter exists in Phase 2.1, so this
    -- CHECK constraint forces safe_for_decision = FALSE for every row. Workers cannot
    -- bypass this at the DB level regardless of application logic.
    -- IMPORTANT for Phase 4/5 implementer: before allowing TRUE values, the truth-adapter
    -- migration must run:
    --   ALTER TABLE public.research_artifacts
    --     DROP CONSTRAINT research_artifacts_safe_for_decision_phase2_chk;
    safe_for_decision                 BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT research_artifacts_safe_for_decision_phase2_chk
        CHECK (safe_for_decision = FALSE),
    deterministic_inputs_allowed      TEXT[] NOT NULL DEFAULT '{}',
    deterministic_inputs_forbidden    TEXT[] NOT NULL DEFAULT '{}',

    -- Advisory text / gaps
    evidence_summary_plain_english    TEXT,
    limitations_or_missing_evidence   TEXT[] NOT NULL DEFAULT '{}',

    -- Typed payload. Column-level CHECK rejects forbidden keys at top level
    -- (fast path). The BEFORE trigger below also scans nested keys recursively
    -- and case-insensitively.
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

COMMENT ON TABLE  public.research_artifacts IS
    'Phase 2.1 Research Artifact Store v1. Sourced research artifacts produced by future workers (Phase 3+). Never sets visible Buy/Hold/Trim/Sell. See docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md.';
COMMENT ON COLUMN public.research_artifacts.user_id IS
    'Owner. RLS scope. Matches intel_v3_snapshots.user_id pattern (no FK on this v3 tier).';
COMMENT ON COLUMN public.research_artifacts.artifact_type IS
    'Bounded enum aligned with the 11+1 skill packs in INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md §3.';
COMMENT ON COLUMN public.research_artifacts.replay_idempotency_key IS
    'Deterministic hash over (skill_pack, scope_kind, ticker_or_scope, source_refs_fingerprint, model_version). UNIQUE per active row to collapse replays. See spec §9.';
COMMENT ON COLUMN public.research_artifacts.confidence_or_trust_level IS
    'Artifact trust, NOT decision conviction. Phase 1 §8 risk #12 forbids using this as conviction.';
COMMENT ON COLUMN public.research_artifacts.safe_for_decision IS
    'Defaults FALSE. DB-locked FALSE in Phase 2.1 by research_artifacts_safe_for_decision_phase2_chk. Workers cannot set TRUE. Phase 4/5 truth-adapter migration must DROP that constraint before allowing TRUE. Never read by decide().';
COMMENT ON COLUMN public.research_artifacts.deterministic_inputs_allowed IS
    'Explicit axis allow-list (e.g. {evidence_axis_band, risk_axis_band}). Advisory; binding only in Phase 5 when per-axis flags are enabled.';
COMMENT ON COLUMN public.research_artifacts.payload IS
    'Typed payload. Forbidden visible-decision keys rejected by column CHECK (top-level, exact) and recursive BEFORE trigger via PL/pgSQL JSONB walker (all depths, case-insensitive). JSONPath keyvalue() was replaced — see §5 hotfix note.';

-- Idempotency: one active artifact per replay_idempotency_key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_research_artifacts_replay_active
    ON public.research_artifacts (replay_idempotency_key)
    WHERE is_active = TRUE;

-- Hot-path indexes for future truth adapter and sweep job.
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
-- One row per source reference. At least one required for any artifact whose
-- facts make a claim about external reality. user_id is denormalized for RLS.

CREATE TABLE IF NOT EXISTS public.research_artifact_sources (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id              UUID NOT NULL REFERENCES public.research_artifacts(id) ON DELETE CASCADE,
    user_id                  UUID NOT NULL,  -- denormalized for RLS; consistency enforced by trigger §5a

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
    source_id                TEXT,           -- e.g. SEC accession number
    source_published_at      TIMESTAMPTZ,
    fetched_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    quote_or_excerpt         TEXT,
    section_reference        TEXT,
    source_hash              TEXT,           -- for dedup

    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.research_artifact_sources IS
    'Provenance/citations for research_artifacts rows. At least one required for any artifact whose facts make external claims.';
COMMENT ON COLUMN public.research_artifact_sources.user_id IS
    'Denormalized from research_artifacts.user_id for RLS without cross-table joins. Consistency enforced by trg_research_artifact_sources_user_consistency.';
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
-- risk_item / catalyst_item / thesis_pillar tables via a fact_kind discriminator
-- + typed structured_payload. Same forbidden-key rules as parent payload.

CREATE TABLE IF NOT EXISTS public.research_artifact_facts (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id              UUID NOT NULL REFERENCES public.research_artifacts(id) ON DELETE CASCADE,
    user_id                  UUID NOT NULL,  -- denormalized for RLS; consistency enforced by trigger §5
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
    period                   TEXT,           -- e.g. '2026Q1'
    as_of                    TIMESTAMPTZ,
    is_quote_grounded        BOOLEAN NOT NULL DEFAULT FALSE,

    -- Typed kind-specific structure. Same forbidden-key rules as parent payload.
    -- Column-level CHECK (top-level exact); recursive trigger (all depths, case-insensitive).
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
    'TRUE when the fact is backed by a quoted source excerpt. HIGH confidence requires at least one quote-grounded fact per spec §6.';

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
-- One row per worker tool call. Required by Phase 1 §5 worker boundary contract:
-- records model id, latency, cost, rejected claims, and digests for replay.

CREATE TABLE IF NOT EXISTS public.worker_audit_events (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- artifact_id: SET NULL when the call was rejected before any artifact row
    -- was written. When non-null, user_id consistency is enforced by trigger §5b.
    artifact_id              UUID REFERENCES public.research_artifacts(id) ON DELETE SET NULL,
    user_id                  UUID NOT NULL,
    worker_run_id            UUID NOT NULL,
    parent_intel_run_id      UUID,

    skill_pack               TEXT NOT NULL,
    tool_call                TEXT NOT NULL,  -- e.g. edgar_fetch, llm_extract
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
    'Per-tool-call audit trail. Required by Phase 1 §5: model id, latency, cost, rejected claims, digests for replay.';
COMMENT ON COLUMN public.worker_audit_events.artifact_id IS
    'NULL when the call was rejected before any artifact row was written. When non-null, user_id must match research_artifacts.user_id (enforced by trigger).';

CREATE INDEX IF NOT EXISTS idx_worker_audit_events_user_run
    ON public.worker_audit_events (user_id, worker_run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_worker_audit_events_user_intel_run
    ON public.worker_audit_events (user_id, parent_intel_run_id, created_at DESC)
    WHERE parent_intel_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_worker_audit_events_artifact
    ON public.worker_audit_events (artifact_id)
    WHERE artifact_id IS NOT NULL;


-- ============================================================================
-- 5. FORBIDDEN-KEY RECURSIVE TRIGGER (research_artifacts + research_artifact_facts)
-- ============================================================================
-- The column-level CHECK constraints above only inspect TOP-LEVEL JSONB keys.
-- This trigger walks the entire payload / structured_payload tree recursively and
-- rejects any forbidden visible-decision key at any nesting depth.
--
-- HOTFIX NOTE: The original implementation used jsonb_path_query(..'lax $.**.keyvalue()').
-- That failed in Supabase/Postgres with:
--   ERROR: jsonpath item method .keyvalue() can only be applied to an object
-- This occurs when lax mode exposes scalar descendants during recursive descent.
-- A recursive CTE fallback was also attempted but PostgreSQL rejected the shape:
--   ERROR: recursive reference to query "walk" must not appear within its non-recursive term
-- The successful fix is the PL/pgSQL recursive JSONB walker below
-- (research_artifact_find_forbidden_jsonb_key), which explicitly branches on
-- jsonb_typeof() and handles object/array/scalar nodes without any JSONPath dependency.
--
-- Case-insensitivity:
--   lower(found_key) = ANY (forbidden_keys) — forbidden_keys are all lowercase.
--   Catches 'Final_Action', 'FINAL_ACTION', 'buy', 'BUY', etc.
--
-- Also enforces child user_id consistency inside the same function body
-- (research_artifact_facts only). research_artifact_sources has its own function.

-- Helper: recursively walks a JSONB value and returns the first forbidden key
-- found at any nesting depth (case-insensitive), or NULL if none found.
-- Replaces JSONPath keyvalue() which fails on scalar descendants in Supabase/Postgres.
CREATE OR REPLACE FUNCTION public.research_artifact_find_forbidden_jsonb_key(target_payload JSONB)
RETURNS TEXT
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
    k      TEXT;
    v      JSONB;
    elem   JSONB;
    result TEXT;
BEGIN
    IF jsonb_typeof(target_payload) = 'object' THEN
        FOR k, v IN SELECT * FROM jsonb_each(target_payload) LOOP
            IF lower(k) = ANY (forbidden_keys) THEN
                RETURN k;
            END IF;
            result := public.research_artifact_find_forbidden_jsonb_key(v);
            IF result IS NOT NULL THEN
                RETURN result;
            END IF;
        END LOOP;
    ELSIF jsonb_typeof(target_payload) = 'array' THEN
        FOR elem IN SELECT * FROM jsonb_array_elements(target_payload) LOOP
            result := public.research_artifact_find_forbidden_jsonb_key(elem);
            IF result IS NOT NULL THEN
                RETURN result;
            END IF;
        END LOOP;
    END IF;
    -- scalar (string, number, boolean, null) — nothing to scan
    RETURN NULL;
END;
$func$;

CREATE OR REPLACE FUNCTION public.research_artifact_reject_forbidden_keys()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $func$
DECLARE
    target_payload JSONB;
    found_key      TEXT;
BEGIN
    IF TG_TABLE_NAME = 'research_artifacts' THEN
        target_payload := NEW.payload;
    ELSIF TG_TABLE_NAME = 'research_artifact_facts' THEN
        target_payload := NEW.structured_payload;
    ELSE
        RETURN NEW;
    END IF;

    -- PL/pgSQL recursive JSONB walker replaces JSONPath keyvalue() (see §5 hotfix note).
    found_key := public.research_artifact_find_forbidden_jsonb_key(
        COALESCE(target_payload, '{}'::jsonb)
    );
    IF found_key IS NOT NULL THEN
        RAISE EXCEPTION
            'Forbidden visible-decision key "%" found in % payload at any nesting depth. '
            'Agents/workers must not store final Buy/Hold/Trim/Sell authority. '
            'See docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md §10 and Phase 1 spec §4.',
            found_key, TG_TABLE_NAME
            USING ERRCODE = 'check_violation';
    END IF;

    -- Defense-in-depth for research_artifact_facts rows.
    IF TG_TABLE_NAME = 'research_artifact_facts' THEN
        -- (a) user_id must match parent artifact.
        IF NEW.user_id IS DISTINCT FROM (
            SELECT user_id FROM public.research_artifacts WHERE id = NEW.artifact_id
        ) THEN
            RAISE EXCEPTION 'research_artifact_facts.user_id must match parent research_artifacts.user_id'
                USING ERRCODE = 'check_violation';
        END IF;

        -- (b) source_id, when non-null, must belong to the same artifact and the
        -- same user. The FK to research_artifact_sources(id) guarantees the row
        -- exists; this check additionally prevents cross-artifact citation smuggling
        -- (e.g. a worker citing a source from another artifact or user).
        IF NEW.source_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM public.research_artifact_sources
                WHERE id          = NEW.source_id
                  AND artifact_id = NEW.artifact_id
                  AND user_id     = NEW.user_id
            ) THEN
                RAISE EXCEPTION
                    'research_artifact_facts.source_id must reference a source '
                    'belonging to the same artifact_id and user_id. '
                    'Cross-artifact citation is not allowed.'
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$func$;

DROP TRIGGER IF EXISTS trg_research_artifacts_reject_forbidden_keys
    ON public.research_artifacts;
CREATE TRIGGER trg_research_artifacts_reject_forbidden_keys
    BEFORE INSERT OR UPDATE ON public.research_artifacts
    FOR EACH ROW
    EXECUTE FUNCTION public.research_artifact_reject_forbidden_keys();

DROP TRIGGER IF EXISTS trg_research_artifact_facts_reject_forbidden_keys
    ON public.research_artifact_facts;
CREATE TRIGGER trg_research_artifact_facts_reject_forbidden_keys
    BEFORE INSERT OR UPDATE ON public.research_artifact_facts
    FOR EACH ROW
    EXECUTE FUNCTION public.research_artifact_reject_forbidden_keys();


-- ============================================================================
-- 5a. USER-ID CONSISTENCY TRIGGER — research_artifact_sources
-- ============================================================================
-- Denormalized user_id must match the parent artifact's user_id.
-- Prevents a misconfigured worker from attaching a source to another user's artifact.

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

DROP TRIGGER IF EXISTS trg_research_artifact_sources_user_consistency
    ON public.research_artifact_sources;
CREATE TRIGGER trg_research_artifact_sources_user_consistency
    BEFORE INSERT OR UPDATE ON public.research_artifact_sources
    FOR EACH ROW
    EXECUTE FUNCTION public.research_artifact_sources_user_consistency();


-- ============================================================================
-- 5b. USER-ID CONSISTENCY TRIGGER — worker_audit_events (non-null artifact_id)
-- ============================================================================
-- Phase 2 draft omitted this check. Added in Phase 2.1 promotion:
-- when artifact_id IS NOT NULL, the audit event's user_id must match the parent
-- artifact's user_id. When artifact_id IS NULL (pre-artifact rejection), the
-- check is skipped — the row may not correspond to any artifact row at all.

CREATE OR REPLACE FUNCTION public.research_artifact_audit_events_user_consistency()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $func$
BEGIN
    IF NEW.artifact_id IS NOT NULL AND NEW.user_id IS DISTINCT FROM (
        SELECT user_id FROM public.research_artifacts WHERE id = NEW.artifact_id
    ) THEN
        RAISE EXCEPTION
            'worker_audit_events.user_id must match parent research_artifacts.user_id '
            'when artifact_id is not null'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$func$;

DROP TRIGGER IF EXISTS trg_worker_audit_events_user_consistency
    ON public.worker_audit_events;
CREATE TRIGGER trg_worker_audit_events_user_consistency
    BEFORE INSERT OR UPDATE ON public.worker_audit_events
    FOR EACH ROW
    EXECUTE FUNCTION public.research_artifact_audit_events_user_consistency();


-- ============================================================================
-- 6. UPDATED_AT MAINTENANCE — research_artifacts only
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
-- 7. ROW LEVEL SECURITY — owner-only, matching intel_v3_snapshots pattern
-- ============================================================================
-- service_role bypasses RLS by Supabase default. Phase 3 workers use service_role.
-- No additional bypass policy is needed.

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


-- ============================================================================
-- 8. GRANTS — matching 002_agent_insights.sql style
-- ============================================================================

GRANT ALL ON public.research_artifacts        TO authenticated, service_role;
GRANT ALL ON public.research_artifact_sources TO authenticated, service_role;
GRANT ALL ON public.research_artifact_facts   TO authenticated, service_role;
GRANT ALL ON public.worker_audit_events       TO authenticated, service_role;


-- ============================================================================
-- ROLLBACK GUIDANCE (commented out — uncomment only if applied and must revert)
-- ============================================================================
-- Drop order: children first (audit events, facts, sources), then parent,
-- then triggers and functions. All four tables are additive — no existing repo
-- table FKs into them — so dropping is safe. The visible Intel v3 path
-- (intel_v3_snapshots, decide(), ReadOnlyEvidenceAdapter, certification detectors,
-- UI cockpit) is unaffected because no existing code reads or writes these tables.
--
-- BEGIN;
-- DROP TRIGGER IF EXISTS trg_worker_audit_events_user_consistency            ON public.worker_audit_events;
-- DROP TRIGGER IF EXISTS trg_research_artifact_sources_user_consistency      ON public.research_artifact_sources;
-- DROP TRIGGER IF EXISTS trg_research_artifact_facts_reject_forbidden_keys   ON public.research_artifact_facts;
-- DROP TRIGGER IF EXISTS trg_research_artifacts_reject_forbidden_keys        ON public.research_artifacts;
-- DROP TRIGGER IF EXISTS trg_research_artifacts_set_updated_at               ON public.research_artifacts;
-- DROP FUNCTION IF EXISTS public.research_artifact_audit_events_user_consistency();
-- DROP FUNCTION IF EXISTS public.research_artifact_sources_user_consistency();
-- DROP FUNCTION IF EXISTS public.research_artifact_reject_forbidden_keys();
-- DROP FUNCTION IF EXISTS public.research_artifact_find_forbidden_jsonb_key(JSONB);
-- DROP FUNCTION IF EXISTS public.research_artifacts_set_updated_at();
-- DROP TABLE   IF EXISTS public.worker_audit_events;
-- DROP TABLE   IF EXISTS public.research_artifact_facts;
-- DROP TABLE   IF EXISTS public.research_artifact_sources;
-- DROP TABLE   IF EXISTS public.research_artifacts;
-- COMMIT;
--
-- NOTE FOR PHASE 4/5 TRUTH-ADAPTER IMPLEMENTER (not a rollback — a forward migration):
-- To allow safe_for_decision = TRUE, run in the truth-adapter migration:
--   ALTER TABLE public.research_artifacts
--     DROP CONSTRAINT IF EXISTS research_artifacts_safe_for_decision_phase2_chk;
-- ============================================================================
-- END 017_research_artifact_store_v1.sql
-- ============================================================================
