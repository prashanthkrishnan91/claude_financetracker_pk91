# Intel v3 — Research Artifact Store v1 (Phase 2 Spec + Draft SQL Proposal)

Status: Phase 2 planning / schema design only. No production migration is applied. No runtime backend or frontend code is changed. No worker, provider, LLM, or UI is touched.

Date: 2026-05-07
Owner: Intel v3 architecture
Severity: Level 2 (architecture / schema foundation, intentionally no runtime behavior change)

References:
- `docs/ai/INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md` — Phase 1 architecture spec (merged in PR #224). Sections §1, §4, §5, §6, §7, §8 are the binding contract for this PR.
- `artifacts/Intel_v3_Architecture_Plan_Draft2_Anthropic_Finance_Agent_Addendum.md` — addendum "Phase 2 - Research Artifact Store v1" listing core objects.
- `v2/database/016_intel_v3_snapshots.sql` — repo migration convention (RLS, indexes, idempotency).
- `docs/ai/sql_drafts/research_artifact_store_v1.sql` — DRAFT-ONLY SQL proposal added in this PR.
- `docs/ai/HANDOFF.md`, `v2/progress_log.md` — current Phase 0/0.5/1 certified state.

---

## 1. Problem Statement

The deterministic Intel v3 visible-decision path is certified (Phase 0/0.5) and the architecture boundary that "agents/LLMs/workers may produce sourced research artifacts only — never visible Buy/Hold/Trim/Sell" is ratified (Phase 1, PR #224). The repo today has no durable, typed, RLS-aware substrate for those artifacts. Without it, any future research worker would have to invent its own storage shape, which is the failure mode the architecture spec explicitly forbids.

Phase 2 designs the simplest durable schema that:

1. Lets future research workers (Phase 3+) write structured, sourced, replayable evidence rows.
2. Gives a future shadow-diagnostics adapter (Phase 4) and a future per-axis deterministic-consumption adapter (Phase 5) one well-typed read surface.
3. Hard-rejects forbidden visible-decision fields at write time so no agent can smuggle Buy/Hold/Trim/Sell authority into the database.
4. Preserves the existing certified visible path (`intel_v3_snapshots`, `intel_v3_snapshot_certification_summary`, `intel_v3_evidence_source_summary`, `ReadOnlyEvidenceAdapter`, `decide()`) without any change.

This is a planning + draft-SQL PR only. No table is created in production. No backend reader/writer exists yet. No worker exists. No UI surface exists.

---

## 2. Non-Goals

- Applying the migration to production. The draft SQL lives at `docs/ai/sql_drafts/research_artifact_store_v1.sql`, not in `v2/database/`. Promoting it requires an explicit follow-on PR with budget and review.
- Implementing any reader (`ReadOnlyEvidenceAdapter` extension) or writer code path.
- Implementing any worker, finance agent, skill pack, or LLM call.
- Adding any provider integration or vendor commitment.
- Changing `decide()` in `decision_policy_v1.py`, `IntelV3Service`, `read_only_evidence_adapter.py`, or any certification detector.
- Changing UI components (`IntelV3Cockpit`, `IntelV3Card`, `IntelV3Drawer`).
- Changing card copy, posture rules, or any visible-action distribution.
- Re-coupling any legacy `recommendation_engine.py` aggregation path.
- Introducing page-load LLM calls or non-zero `attempted_llm_calls`.
- Vendor selection (filing/transcript/calendar/news/fundamentals providers). Phase 1 §3.12 stance preserved.
- Designing the deterministic-consumption truth adapter (Phase 4/5 work).
- Designing UI source-trail surfacing (Phase 6 work).
- Storing free-form, unauditable blob payloads without typed top-level governance.

---

## 3. Architecture Fit With Phase 1 Skill Pack Audit

The Phase 1 spec defines four binding contracts that this Phase 2 schema must satisfy. Each is mapped to an enforcement point:

| Phase 1 contract | Enforcement in this Phase 2 design |
|---|---|
| §1.1 `decide()` is sole visible-action authority. | This store has no `final_action` / `final_conviction` / `final_allocation` / `deploy_amount` / `buy/hold/trim/sell` columns or JSON keys. CHECK constraint + BEFORE trigger reject those keys at write time. |
| §1.3 Workers respect the truth/evidence contract. | Every artifact row carries `confidence_or_trust_level`, `freshness_status`, and `limitations_or_missing_evidence`. A `safe_for_decision` boolean defaults to `false` and is only ever flipped by a future truth-adapter, never by a worker. |
| §1.4 LLM outputs must be source-grounded, replayable, and provenance-bearing. | `research_artifact_sources` is required for any non-empty `research_artifact_facts`. `replay_idempotency_key UNIQUE` collapses re-runs. `worker_audit_events` records model id, latency, cost, rejected claims, and digests for replay. |
| §1.5 Visible cockpit reads only `intel_v3_snapshots`. | This phase adds NO read path into the cockpit. `ReadOnlyEvidenceAdapter` is unchanged. `intel_v3_snapshot_response_summary` and `intel_v3_snapshot_certification_summary` payloads are unchanged. |
| §4 Forbidden artifact fields. | Hard rule: `payload` JSONB CHECK rejects any of `final_action`, `buy`, `sell`, `trim`, `hold`, `final_conviction`, `final_allocation`, `deploy_amount`, `deploy_dollar`, `deploy_shares` at the top-level. A BEFORE INSERT/UPDATE trigger scans nested object keys recursively. |
| §5 Worker boundary. | Workers may INSERT into `research_artifacts`, `research_artifact_sources`, `research_artifact_facts`, `worker_audit_events`. Workers may NOT touch `intel_v3_snapshots`, `recommendations`, `agent_runs`, `agent_insights`, `decision_history`, or any other existing table. RLS scopes writes to `auth.uid()`. |
| §7 Acceptance criteria for future PRs. | Schema is designed so that every criterion (no LLM final action, no page-load LLM, no legacy re-coupling, no raw metric keys in UI, no fake precision, no unsourced claims, replayable, idempotent, freshness handled, forbidden fields rejected) maps to a column, constraint, or absence of a column. |
| §8 Risk register. | Each risk is mapped to a column or absence in §6 of this doc. |

The schema fits cleanly in the existing v3 tier (parallels `intel_v3_snapshots`'s pattern: `id UUID PK`, `user_id UUID NOT NULL`, RLS owner-only, additive). It does NOT inherit from or join to `agent_runs` / `agent_insights` (those are legacy aggregation inputs, decoupled per PR #220).

---

## 4. Proposed Schema Overview

Four tables, normalized but compact. The addendum lists `sourced_claim`, `metric_observation`, `risk_item`, `catalyst_item`, `thesis_pillar`, and `audit_event` as separate concepts — Phase 2 collapses the first five into a single typed `research_artifact_facts` table with a `fact_kind` discriminator + a typed `structured_payload`. This avoids a 6-table fan-out where each table would carry the same provenance/RLS/index machinery, while still preserving the addendum's typed-observation vocabulary.

### 4.1 `research_artifacts` (parent)

One row per artifact a worker produces. Carries governance, provenance, freshness, idempotency, deterministic-consumption gating, and a sanitized plain-English summary. Forbidden visible-decision fields are rejected at write time.

Key columns:
- `id UUID PRIMARY KEY` — artifact_id.
- `user_id UUID NOT NULL` — RLS scope (matches `intel_v3_snapshots` convention; no FK to `public.users` on this v3 tier).
- `artifact_schema_version TEXT NOT NULL DEFAULT 'artifact.v1'`.
- `artifact_type TEXT NOT NULL` — bounded set (filing_risk, catalyst_window, valuation_context, fundamental_quality, capital_allocation, risk_red_team, analyst_revisions, news_event, etf_fund_note, portfolio_exposure, hidden_gem_candidate, thesis_update). Matches Phase 1 §3.1–§3.11 skill packs.
- `skill_pack TEXT NOT NULL` — same value as the Phase 1 skill pack name.
- `scope_kind TEXT NOT NULL` — `ticker | portfolio | watchlist_candidate`.
- `ticker TEXT` — populated when scope is ticker or watchlist candidate.
- `generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
- `source_window_start TIMESTAMPTZ`, `source_window_end TIMESTAMPTZ` — `as_of_start/end`.
- `generated_by_worker TEXT NOT NULL` — worker identity.
- `generated_by_model TEXT`, `model_version TEXT` — only when LLM-touched; nullable for non-LLM workers.
- `input_fingerprint TEXT NOT NULL` — digest over normalized worker input.
- `replay_idempotency_key TEXT NOT NULL` — deterministic key over `(skill_pack, scope_kind, ticker_or_scope, source_refs_hash, model_version)`. UNIQUE per active row.
- `confidence_or_trust_level TEXT NOT NULL` — `HIGH | MEDIUM | LOW | UNKNOWN`. Defaults `LOW`.
- `freshness_status TEXT NOT NULL` — `FRESH | STALE | UNKNOWN`. Defaults `UNKNOWN`.
- `expires_at TIMESTAMPTZ` — optional TTL.
- `invalidated_at TIMESTAMPTZ`, `invalidation_reason TEXT` — explicit invalidation.
- `is_active BOOLEAN NOT NULL DEFAULT TRUE`.
- `safe_for_decision BOOLEAN NOT NULL DEFAULT FALSE` — only the future truth-adapter may flip; workers must not.
- `deterministic_inputs_allowed TEXT[]` — explicit axis allow-list (e.g. `{evidence_axis_band, risk_axis_band}`).
- `deterministic_inputs_forbidden TEXT[]` — explicit axis deny-list.
- `evidence_summary_plain_english TEXT` — short, sanitized plain-English summary.
- `limitations_or_missing_evidence TEXT[]`.
- `parent_intel_run_id UUID` — link to the visible `intel_v3` run that triggered the worker, if any.
- `worker_run_id UUID NOT NULL` — opaque run id from the worker.
- `payload JSONB NOT NULL DEFAULT '{}'::jsonb` — typed payload; subject to forbidden-key CHECK + trigger.
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.

### 4.2 `research_artifact_sources` (citations / provenance)

One row per source reference. At least one row required for any artifact whose facts make a claim about external reality.

Key columns:
- `id UUID PRIMARY KEY`.
- `artifact_id UUID NOT NULL REFERENCES research_artifacts(id) ON DELETE CASCADE`.
- `user_id UUID NOT NULL` — denormalized for RLS simplicity (matches existing repo pattern of carrying `user_id` on child tables).
- `source_kind TEXT NOT NULL` — `sec_filing | transcript | vendor_calendar | news | vendor_fundamentals | vendor_estimates | peer_set_def | press_release | company_disclosure | other`.
- `provider_name TEXT NOT NULL`, `provider_version TEXT`.
- `source_url TEXT`, `source_id TEXT` — e.g. SEC accession number.
- `source_published_at TIMESTAMPTZ`, `fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
- `quote_or_excerpt TEXT` — the supporting quote backing any extracted claim.
- `section_reference TEXT`.
- `source_hash TEXT` — for dedup.
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.

### 4.3 `research_artifact_facts` (typed structured observations)

One row per typed fact. Replaces `sourced_claim`, `metric_observation`, `risk_item`, `catalyst_item`, `thesis_pillar` from the addendum via a discriminator. A typed `structured_payload` JSONB carries the kind-specific fields; the same forbidden-key rules apply.

Key columns:
- `id UUID PRIMARY KEY`.
- `artifact_id UUID NOT NULL REFERENCES research_artifacts(id) ON DELETE CASCADE`.
- `user_id UUID NOT NULL`.
- `fact_kind TEXT NOT NULL` — `metric_observation | risk_item | catalyst_item | thesis_pillar | sourced_claim | event | peer_context | quality_observation | revision_note`.
- `axis_hint TEXT` — which axis this fact MAY influence later (`evidence | risk | price | quality | catalyst | exposure`). Advisory, not binding.
- `severity TEXT` — `LOW | MEDIUM | HIGH | UNKNOWN` (only used by `risk_item` / `event`; nullable otherwise).
- `period TEXT` — e.g. `2026Q1`.
- `as_of TIMESTAMPTZ`.
- `is_quote_grounded BOOLEAN NOT NULL DEFAULT FALSE`.
- `source_id UUID REFERENCES research_artifact_sources(id) ON DELETE SET NULL` — the citation backing this fact.
- `structured_payload JSONB NOT NULL DEFAULT '{}'::jsonb` — typed by `fact_kind`.
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.

### 4.4 `worker_audit_events`

One row per worker tool call. Implements the "audit_event" object from the addendum and the §5 worker boundary obligation to record model id, latency, cost, and rejected claims.

Key columns:
- `id UUID PRIMARY KEY`.
- `artifact_id UUID REFERENCES research_artifacts(id) ON DELETE SET NULL` — null when the call was rejected before any artifact was written.
- `user_id UUID NOT NULL`.
- `worker_run_id UUID NOT NULL`.
- `parent_intel_run_id UUID`.
- `skill_pack TEXT NOT NULL`.
- `tool_call TEXT NOT NULL` — e.g. `edgar_fetch`, `transcript_provider`, `llm_extract`, `peer_lookup`.
- `input_digest TEXT`, `output_digest TEXT`.
- `model_id TEXT`, `model_version TEXT`.
- `latency_ms INTEGER`.
- `cost_estimate_usd NUMERIC(12,6)`.
- `status TEXT NOT NULL` — `completed | failed | rejected | timeout | cost_capped`.
- `rejected_claims JSONB` — list of `{claim, reason}`.
- `error_message TEXT`.
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.

### 4.5 What is intentionally NOT added

- No `final_action`, `buy / sell / trim / hold`, `final_conviction`, `final_allocation`, `deploy_amount`, `deploy_dollar`, `deploy_shares` columns or JSON keys (Phase 1 §4 hard rule).
- No FK from any new table into `recommendations`, `agent_runs`, `agent_insights`, `intel_v3_snapshots`, `decision_history`, or `decision_logs_*`. This store is independent from the visible-decision pipeline; coupling, when introduced, must go through the truth adapter, not via FK.
- No "trust score numeric" column distinct from `confidence_or_trust_level` — Phase 1 §8 risk #12 (implicit conviction smuggling) explicitly forbids using artifact trust as decision conviction.
- No write-side authority for `safe_for_decision` from workers — that flag is set by future truth-adapter logic only.
- No "approved" / "blessed" boolean — Phase 2 has no human-review surface.

---

## 5. Artifact Lifecycle

```
[worker run starts]
    └─► writes worker_audit_events row (status=pending or completed-step)
        └─► extracts facts with at least one source_ref each
            └─► computes replay_idempotency_key
                └─► INSERT INTO research_artifacts (... is_active=true,
                            confidence_or_trust_level <= LOW by default,
                            freshness_status, expires_at,
                            safe_for_decision=false)
                └─► INSERT INTO research_artifact_sources (...)
                └─► INSERT INTO research_artifact_facts (...)
                └─► UPDATE worker_audit_events SET artifact_id=...

[idempotency collision on replay_idempotency_key]
    └─► do not duplicate; either update timestamps and counters,
        or supersede previous active row by setting is_active=false.

[freshness expires]
    └─► scheduled task (Phase 3+) sets freshness_status=STALE
        when NOW() > expires_at, or when source_window_end is older than
        skill-pack-defined window.
    └─► is_active stays true; truth adapter (Phase 4+) treats STALE as not-safe.

[explicit invalidation]
    └─► UPDATE research_artifacts SET invalidated_at=NOW(),
                invalidation_reason='...', is_active=false.
    └─► no DELETE — historical artifacts are kept for replay/audit.

[visible-decision consumption — Phase 4/5 only]
    └─► future truth adapter (NOT a worker) reads (is_active=true,
            invalidated_at IS NULL, freshness_status='FRESH',
            confidence_or_trust_level >= configured threshold,
            and the fact's axis_hint is in deterministic_inputs_allowed).
    └─► adapter may then set safe_for_decision=true on the artifact
            ONLY if the per-axis flag is enabled AND the artifact passed
            the truth/evidence contract.
    └─► decide() never reads research_artifacts directly; it only consumes
            adapter-emitted DecisionInputV3 fields (e.g. risk_axis_band,
            evidence_axis_band) under the existing truth contract.
```

This lifecycle is documented for Phase 3/4/5 implementers. None of it is built in this PR.

---

## 6. Trust Model

Three layers of trust, none of which alone gives an artifact decision authority:

1. **Source trust** — `research_artifact_sources.source_kind` and `provider_name`. SEC filings and licensed transcripts are first-class sources; free news is not. A fact that is not `is_quote_grounded` and lacks a `source_url` cannot be HIGH confidence.
2. **Artifact trust** — `confidence_or_trust_level` ∈ `{HIGH, MEDIUM, LOW, UNKNOWN}` set by the worker. Workers must default to LOW when sources are missing/weak/stale (§5 fail-closed). HIGH requires (a) at least one quote-grounded fact, (b) all required `limitations_or_missing_evidence` resolved, (c) `freshness_status=FRESH`.
3. **Decision-eligibility** — `safe_for_decision` boolean. Defaults `false`. Only the future truth adapter may set it to `true`, and only when the per-axis flag (Phase 5) is enabled AND `confidence_or_trust_level >= MEDIUM` AND `freshness_status = FRESH` AND `invalidated_at IS NULL`. Workers cannot set this column.

`confidence_or_trust_level` is artifact trust, not decision conviction. Phase 1 §8 risk #12 forbids using it as conviction. The deterministic policy retains exclusive ownership of conviction.

---

## 7. Deterministic Intel v3 Consumption (Future, Not in This PR)

This PR does NOT add any read path. The future Phase 4/5 read path is documented here so that the schema decisions are traceable.

1. A future Phase 4 read adapter (sibling to `existing_signal_truth_adapter`, NOT a modification to it in Phase 4) reads from `research_artifacts` joined to `research_artifact_facts` (and optionally `research_artifact_sources`).
2. The adapter applies four filters: `is_active = TRUE`, `invalidated_at IS NULL`, `freshness_status = 'FRESH'`, `confidence_or_trust_level >= MEDIUM`.
3. The adapter then, for each fact, checks `axis_hint` against the artifact's `deterministic_inputs_allowed` list and the per-axis flag (e.g. `RESEARCH_ARTIFACT_AXIS_RISK_ENABLED`).
4. The adapter emits structured axis hints into `DecisionInputV3` fields ONLY (e.g. `risk_axis_band`, `evidence_axis_band`). No artifact field reaches `_build_rationale()` without first passing through the existing `_clean_evidence_text()` sanitization.
5. `decide()` is unchanged — it sees only `DecisionInputV3` and is unaware of any artifact source.
6. The adapter may set `safe_for_decision = TRUE` on artifacts it consumed, for audit; this column is never used by `decide()` itself.

The visible cockpit continues to read only from `intel_v3_snapshots`. This is enforced today by the static-source guards in `test_intel_v3_phase0_5_regression_guardrail.py` and the §1.5 Phase 1 contract; both stay green.

---

## 8. Data Freshness / Staleness Policy

Freshness rules are skill-pack-specific. The schema supports the policy without baking specific TTLs:

- Every artifact carries `source_window_start`, `source_window_end`, and an optional `expires_at`.
- A scheduled task (Phase 3+) sets `freshness_status='STALE'` when:
  - `NOW() > expires_at`, OR
  - The most recent qualifying source per skill pack is newer than `source_window_end` (e.g. for filing-risk: a newer 10-Q has been published), OR
  - `source_published_at` is older than the per-skill-pack max age.
- `freshness_status` may also be `UNKNOWN` when the worker cannot determine freshness (e.g. provider stale, vendor calendar drift).
- The truth adapter treats `STALE` and `UNKNOWN` as not-safe-for-decision unless an explicit per-skill-pack rule allows otherwise.

Index `idx_research_artifacts_user_freshness` supports the scheduled sweep efficiently.

---

## 9. Idempotency / Replay Strategy

- `replay_idempotency_key` is a deterministic hash over: `(skill_pack, scope_kind, ticker_or_scope, source_refs_fingerprint, model_version)`. Workers compute it before write.
- Constraint: `UNIQUE (replay_idempotency_key) WHERE is_active = TRUE`. A second worker run on the same input collapses by either updating the existing row's `generated_at`/`updated_at` (no-op semantics) or by setting the prior row `is_active=false` and inserting a new active row. The choice is per-skill-pack (Phase 3 worker decision); both options are supported by the schema.
- Every `worker_audit_events` row carries `input_digest` and `output_digest` so a replay-from-audit can be reproduced.
- `parent_intel_run_id` links artifacts to the visible run that triggered the worker (if any), enabling per-run replay.
- No DELETE path. Invalidation is always logical (`invalidated_at`, `invalidation_reason`, `is_active=false`).

---

## 10. Security / RLS Considerations

- All four tables `ENABLE ROW LEVEL SECURITY` (matches `intel_v3_snapshots`).
- One owner-only policy per table: `USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())`.
- `service_role` bypasses RLS by default in Supabase, which is the intended Phase 3 worker access path; no additional bypass policy needed.
- `user_id` is denormalized onto child tables (`research_artifact_sources`, `research_artifact_facts`, `worker_audit_events`) so RLS does not require a cross-table subquery on every read.
- A trigger validates that `user_id` on child rows matches the parent `research_artifacts.user_id` (defense in depth against a misconfigured worker).
- Forbidden visible-decision keys are rejected at write time via:
  - A column-level `CHECK (NOT (payload ?| ARRAY[...]))` for top-level keys.
  - A BEFORE INSERT/UPDATE trigger that recursively scans `payload` and `structured_payload` JSONB for any forbidden key at any nesting depth, raising an exception with a clear message and logging via NOTICE.
- No PII or secrets are stored. `provider_name` and `model_id` are not credentials.

---

## 11. Migration / Runbook Notes

This PR does NOT apply a migration. The draft SQL is at `docs/ai/sql_drafts/research_artifact_store_v1.sql` and is clearly marked DRAFT ONLY in its file header.

Promotion path (when approved in a future PR — NOT this PR):

1. Re-review the draft against this spec doc and the Phase 1 spec.
2. Move the file to `v2/database/017_research_artifact_store_v1.sql` (next migration number).
3. Apply via Supabase SQL Editor in a maintenance window. The migration is purely additive — no existing table is modified and no column is dropped.
4. Verify the four new tables exist and RLS is enabled (`SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('research_artifacts', 'research_artifact_sources', 'research_artifact_facts', 'worker_audit_events');`).
5. Verify the forbidden-keys trigger rejects a synthetic insert with `payload = '{"final_action": "BUY"}'::jsonb`.
6. Verify zero impact on the existing visible certification. Run `intel_v3_snapshot_certification_summary` once and confirm fields are unchanged.

Rollback (only if applied and an issue is found):

1. The draft SQL has a clearly marked DRAFT ROLLBACK section that drops all four tables. It is commented out by default and must be uncommented before use.
2. Because the migration is additive and no other table FKs into the new tables, dropping them is safe.

---

## 12. Open Questions

1. **`replay_idempotency_key` collapse semantics per skill pack.** Should an idempotent re-run no-op (preferred for unchanged sources) or supersede (preferred for refreshed sources)? Phase 3 single-worker scaffold should pick a default; schema supports both.
2. **`worker_audit_events.cost_estimate_usd` precision.** `NUMERIC(12,6)` accommodates fractional-cent costs; revisit if real provider costs require more digits.
3. **Hot-path indexing for the future truth adapter.** The proposed indexes target `(user_id, ticker, is_active, generated_at DESC)` and `(user_id, artifact_type, generated_at DESC)`. Phase 4 may need an additional partial index when read patterns are known.
4. **Cardinality cap per (user, ticker).** No hard cap is enforced today. Phase 3 should add a sanity check (e.g. retain N most recent active artifacts per `(user_id, ticker, artifact_type)`).
5. **Vendor selection.** Out of scope here per Phase 1 §3.12. The schema does not depend on any specific provider.
6. **Artifact text length caps.** `evidence_summary_plain_english` is `TEXT` with no DB-level cap; Phase 3 worker-side validation should enforce a length bound to keep UI sanitization predictable later.
7. **Recursive forbidden-key scan performance.** The trigger uses `jsonb_each` recursion; for typical artifact payload sizes this is negligible. If real-world payloads grow, the trigger may move to a stricter top-level JSON Schema check.
8. **Per-axis flag ergonomics.** Phase 5 will introduce `RESEARCH_ARTIFACT_AXIS_<NAME>_ENABLED` flags. The schema's `deterministic_inputs_allowed` array already supports this, but operational ergonomics (e.g. an admin UI to flip flags) is Phase 5 work.

---

## 13. Validation Checklist (this PR)

- [x] No runtime files changed. `git diff` covers only `docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md`, `docs/ai/sql_drafts/research_artifact_store_v1.sql`, `docs/ai/sql_drafts/README.md`, `docs/ai/HANDOFF.md`, `v2/progress_log.md`.
- [x] No production migration added. Draft SQL is at `docs/ai/sql_drafts/`, NOT `v2/database/`.
- [x] Draft SQL header carries `DRAFT ONLY — DO NOT APPLY TO PRODUCTION UNTIL APPROVED`.
- [x] No backend reader/writer integration. `ReadOnlyEvidenceAdapter`, `IntelV3Service`, `decision_policy_v1.py`, `existing_signal_adapter.py`, `existing_signal_truth_adapter.py`, `source_validator_lite.py` are unchanged.
- [x] No worker implementation. No `agents/`, `workers/`, or skill-pack code added.
- [x] No finance agents, providers, or LLM integrations added.
- [x] No UI behavior change. `IntelV3Cockpit`, `IntelV3Card`, `IntelV3Drawer` and all frontend code unchanged.
- [x] No `decide()` visible decision change. `decision_policy_v1.py` unchanged.
- [x] No certification detector change. `source_validator_lite.py` and `test_intel_v3_phase0_5_regression_guardrail.py` unchanged.
- [x] No legacy `recommendation_engine` re-coupling. Static guards stay green.
- [x] No page-load LLM call introduced. `attempted_llm_calls=0`, `page_load_llm_calls=0` invariant preserved.
- [x] Forbidden-field deny-list (`final_action`, `buy/hold/trim/sell`, `final_conviction`, `final_allocation`, `deploy_amount`, `deploy_dollar`, `deploy_shares`) is encoded in the draft SQL CHECK constraint and BEFORE trigger.
- [x] RLS assumptions documented (§10) and reflected in draft SQL.
- [x] HANDOFF.md and v2/progress_log.md updated with concise Phase 2 entries.

---

## 14. Phase 2.1 / Phase 3 Recommended Next Steps

Phase 2.1 — Draft SQL review + promotion (separate PR):
1. Senior review of `docs/ai/sql_drafts/research_artifact_store_v1.sql` against this spec.
2. Resolve §12 open questions where they affect schema (idempotency collapse semantics; cardinality cap).
3. Promote draft to `v2/database/017_research_artifact_store_v1.sql`, apply in Supabase, verify.
4. Add a single test-only verification script (no runtime reader) that asserts: tables exist, RLS enabled, forbidden-key trigger rejects synthetic violation.

Phase 3 — Single narrow research worker (Earnings Reviewer recommended), per Phase 1 §6 Phase 3:
1. Off-path, flag-gated worker writing to the new tables only.
2. Golden-fixture replay test, idempotency test, "no source → no claim" test.
3. Kill-switch flag and runbook.
4. Zero change to `decide()`, `IntelV3Service`, `ReadOnlyEvidenceAdapter`, certification detectors, or visible UI.

Phase 4 onward continues per the Phase 1 roadmap. No deviation is proposed.

---

## 15. Self-Audit Map (this PR)

| Acceptance criterion (from prompt) | File | Section / Constraint | Enforcement | Limitation |
|---|---|---|---|---|
| 1. Tables proposed | `docs/ai/sql_drafts/research_artifact_store_v1.sql` | §1–§4 of file | DDL `CREATE TABLE IF NOT EXISTS` for 4 tables | Draft only; not applied. |
| 2. Columns with types | same | each table block | Typed columns + DEFAULTs | — |
| 3. Primary keys | same | every `id UUID PRIMARY KEY` | DDL | — |
| 4. Foreign keys where safe | same | child `artifact_id REFERENCES research_artifacts(id) ON DELETE CASCADE`; `source_id` SET NULL | DDL | Intentionally no FK back to legacy/visible tables. |
| 5. Unique/idempotency | same | `UNIQUE (replay_idempotency_key) WHERE is_active = TRUE` | partial unique index | Per-skill-pack collapse semantics deferred. |
| 6. Useful indexes | same | `idx_research_artifacts_user_*`, `idx_research_artifact_sources_artifact`, `idx_research_artifact_facts_*`, `idx_worker_audit_events_*` | DDL | Phase 4 may add hot-path index. |
| 7. RLS enablement/policies | same | `ENABLE ROW LEVEL SECURITY` + `<table>_owner` policy | DDL + `DO $$` guard | Service role bypass relies on Supabase default. |
| 8. Comments on critical fields | same | `COMMENT ON COLUMN ...` blocks | DDL | — |
| 9. Rollback / drop section | same | clearly marked, commented-out DROP TABLE block at end | DDL (inactive) | Must be uncommented to use. |
| 10. DRAFT-ONLY header | same | first banner of the SQL file | enforced by review | Not a runtime guard. |
| Spec §1 problem statement | this doc | §1 | — | — |
| Spec §2 non-goals | this doc | §2 | — | — |
| Spec §3 architecture fit | this doc | §3 | mapping table to Phase 1 contracts | — |
| Spec §4 schema overview | this doc | §4 | matches SQL | — |
| Spec §5 lifecycle | this doc | §5 | — | Future work; not coded. |
| Spec §6 trust model | this doc | §6 | — | — |
| Spec §7 deterministic consumption | this doc | §7 | — | Phase 4/5 work. |
| Spec §8 freshness | this doc | §8 | `freshness_status`, `expires_at`, sweep index | Sweep job is Phase 3+. |
| Spec §9 idempotency / replay | this doc | §9 | `replay_idempotency_key`, audit digests | Collapse semantics per skill pack. |
| Spec §10 security / RLS | this doc | §10 | RLS + forbidden-key trigger | Recursive scan perf monitored. |
| Spec §11 migration / runbook | this doc | §11 | — | Promotion is a separate PR. |
| Spec §12 open questions | this doc | §12 | — | 8 questions tracked. |
| Spec §13 phase 2.1 / 3 next steps | this doc | §14 | — | — |
| Forbidden-field hard rule | SQL | column CHECK on `payload`, `structured_payload`; BEFORE trigger; bounded `artifact_type` and `fact_kind` enums | DDL | Trigger scans nested JSON; LIST-OF-OBJECTS payload patterns supported. |

---

## 16. Out of Scope (this PR — restated)

- Any runtime backend or frontend code change.
- Any production SQL migration application.
- Any provider, LLM, agent, or worker integration.
- Any change to certification detectors, test thresholds, or visible card distribution.
- Any change to `decide()`, `IntelV3Service`, `ReadOnlyEvidenceAdapter`, `existing_signal_*adapter*`, or any UI surface.
- Any vendor selection or commitment.
- Any Deploy integration.
- Any change to legacy `recommendation_engine.py` or its decoupling.

If any of the above is required to make this design actionable, the answer is to split the work into the next phase (Phase 2.1 or Phase 3), not to widen this PR.
