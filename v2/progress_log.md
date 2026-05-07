
## 2026-05-07 — Phase 2.1: Research Artifact Store v1 — Migration Promotion (Level 2, SQL File Only, Not Applied)

- Phase 0, Phase 0.5, Phase 1, and Phase 2 are closed. This PR promotes the Phase 2 draft SQL (`docs/ai/sql_drafts/research_artifact_store_v1.sql`) into a real production migration: `v2/database/017_research_artifact_store_v1.sql`. No production Supabase SQL is applied — manual apply required after merge with explicit approval.
- Migration is additive only. Four new tables: `research_artifacts`, `research_artifact_sources`, `research_artifact_facts`, `worker_audit_events`. All `IF NOT EXISTS`/`DO $$` guarded.
- Phase 2.1 promotion changes vs. draft: (1) `worker_audit_events` user_id consistency trigger added (was missing for non-null artifact_id rows); (2) JSONPath column alias renamed from ambiguous `t(value)` to `kv(obj)`; (3) PG 14+ compatibility and case-insensitivity documented in comments.
- Forbidden-key enforcement: column-level JSONB CHECK (top-level, exact-lowercase) + recursive BEFORE trigger via `jsonb_path_query(... 'lax $.**.keyvalue()')` (case-insensitive, all depths).
- RLS: owner-only policies on all four tables. service_role bypasses by Supabase default (Phase 3 worker path). Grants: `authenticated` and `service_role`.
- Rollback block: commented out, drop-children-first order, inside migration file.
- Architecture rule reinforced: workers write artifacts/audit only; `decide()` in `decision_policy_v1.py` remains the sole Buy/Hold/Trim/Sell authority; `safe_for_decision` defaults FALSE; no page-load LLM calls; no legacy recommendation_engine re-coupling.
- Validation: `git diff` shows zero changes under `v2/backend/`, `v2/frontend/`, or any runtime code. No `decide()` / `IntelV3Service` / certification detector changes.
- Supabase SQL: Yes — migration file added, NOT APPLIED (manual apply required after merge).
- Next: Phase 3 single narrow worker dark-run scaffold (Earnings Reviewer), after Supabase apply confirmed.

## 2026-05-07 — Phase 2: Research Artifact Store v1 — Planning + Draft SQL (Level 2, Docs/Draft Only)

- Phase 0 (PRs #220–#222), Phase 0.5 (PR #223), and Phase 1 (PR #224) are closed and certified. This PR is planning + draft-SQL only — no runtime code, no production migration, no providers, no LLM, no UI, no `decide()` change.
- New spec doc: `docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md`. Sections: problem statement, non-goals, architecture fit with Phase 1 binding contracts, 4-table schema overview, artifact lifecycle, trust model, future deterministic-consumption read path (Phase 4/5), freshness/staleness, idempotency/replay, security/RLS, migration/runbook, open questions (8), Phase 2.1/Phase 3 next steps, validation checklist, self-audit map, restated out-of-scope.
- New draft SQL: `docs/ai/sql_drafts/research_artifact_store_v1.sql`. DRAFT ONLY header. 4 additive tables — `research_artifacts` (parent), `research_artifact_sources` (citations), `research_artifact_facts` (typed observations via `fact_kind` discriminator), `worker_audit_events` (audit trail). Forbidden visible-decision keys (`final_action`, `buy/sell/trim/hold`, `final_conviction`, `final_allocation`, `deploy_amount`, `deploy_dollar`, `deploy_shares`) rejected at write time by a column-level JSONB CHECK plus a recursive BEFORE INSERT/UPDATE trigger that walks every nested key. Idempotency: `UNIQUE (replay_idempotency_key) WHERE is_active = TRUE`. RLS: owner-only via `auth.uid() = user_id`, mirroring `intel_v3_snapshots`. Indexes, comments on critical columns, and a clearly-marked commented-out DRAFT ROLLBACK section included.
- New convention doc: `docs/ai/sql_drafts/README.md`. Explains the draft-only directory — drafts live outside `v2/database/` so the standard apply path cannot pick them up by accident.
- HANDOFF.md updated with the same summary and the next recommended PR (Phase 2.1 review/promotion or Phase 3 single-worker dark-run scaffold).
- Architecture rule reinforced: agents/workers may write only sourced artifacts and audit events; they may not touch `intel_v3_snapshots`, `recommendations`, `agent_runs`, `agent_insights`, or the deterministic decision pipeline. `decide()` remains the sole visible decision authority. No page-load LLM calls. No legacy `recommendation_engine` re-coupling.
- Validation: docs / draft SQL only; `git diff` shows zero changes under `v2/backend/`, `v2/frontend/`, or `v2/database/`. Phase 0/0.5/1 certification surfaces untouched.
- Supabase SQL: Yes — DRAFT ONLY (not applied).
- Next strategic phase: Phase 2.1 review + promotion (resolve idempotency-collapse semantics, cardinality cap; move file to `v2/database/017_*` and apply), or Phase 3 single narrow worker dark-run scaffold per Phase 1 §6 / §9.2.

## 2026-05-07 — Phase 1: Finance Agent Skill Pack Audit (Spec Only, Level 1)

- Phase 0 (PRs #220–#222) and Phase 0.5 (PR #223) certified. This PR adds the Phase 1 architecture spec only — no runtime code, no SQL, no providers, no LLM, no UI changes.
- New doc: `docs/ai/INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md`. Defines: executive decision boundary (deterministic Intel v3 policy is sole Buy/Hold/Trim/Sell authority; agents may produce sourced research artifacts only), current-state repo audit (snapshot path, run/snapshot separation, ReadOnlyEvidenceAdapter role, certification tests, decoupled legacy paths, reusable seams), 11 skill packs with allowed/forbidden outputs and target phases, Research Artifact Contract v1 in prose (with forbidden-field hard rule), worker boundary contract, 6-phase roadmap with test/production/rollback gates, acceptance criteria for all future implementation PRs, risk register, and 4 short prompt templates for later phases.
- HANDOFF.md updated with the same summary and the next recommended PR (Phase 2 — Research Artifact Store v1 planning + draft SQL proposal).
- Architecture rule reinforced: agents are research artifact workers only. `decide()` in `decision_policy_v1.py` remains the sole visible decision authority. No re-coupling to legacy `recommendation_engine`. No page-load LLM calls.
- Validation: docs-only; `git diff` confirms no runtime/SQL/UI changes. Phase 0/0.5 certification surfaces untouched.
- Next strategic phase: Phase 2 — Research Artifact Store v1 planning + draft SQL proposal (no migration applied).

## 2026-05-06 — Phase 0.5: Intel v3 Regression Guardrails (Level 1)

- Phase 0 certified (PRs #220–#222). This PR adds regression guardrails only — no visible behavior changes.
- New file: `test_intel_v3_phase0_5_regression_guardrail.py` (30 tests).
- Adds reusable `assert_snapshot_certification_clean()` helper: any future snapshot test can call it to catch all Phase 0 contract regressions in one place.
- Static source guards: verify `intel_v3_service.py` and `read_only_evidence_adapter.py` do not reference `get_insight_cards`, `_compute_insight_cards`, `recommendation_engine`, or any LLM import.
- Log-format contract: `source_path=intel_v3_snapshot`, `generated_legacy_recommendations=false`, `attempted_llm_calls=0`, `page_load_llm_calls=0` verified present in service source.
- Evidence stats key contract: `certify_snapshot_cards()` returns all required keys as ints.
- 138 existing v3 tests still pass. No SQL. No frontend changes. No provider/LLM changes.
- Next strategic phase: Finance Agent Skill Pack Audit — planning only, not implementation.

## 2026-05-06 — Intel v3 Evidence-Aware Rationale + Certification Hardening (Level 2)

- PR #217 passed plumbing certification but failed intelligence quality: ticker-prefix boilerplate slipped through exact-duplicate detection. Production cards read: "MSFT: strong evidence and fairly priced. Portfolio has room to add. Manageable risk." — same sentence for every BUY ticker.
- Root cause: `_build_rationale()` never used `primary_driver`, `risk_flag`, `action_reason`, `analyst_drivers` from the InsightCard. These evidence fields existed but were not threaded into DecisionInputV3.
- This PR introduces evidence-aware v3 visible rationale: BUY cards use `primary_driver` when available; HOLD cards explain the specific reason (thin evidence / risk / on-target / price stretched).
- Strengthened certification: `certify_snapshot_cards()` now reports `repeated_skeleton_count`, `ticker_prefix_only_reason_count`, `weak_buy_rationale_count` in addition to existing fields.
- New functions: `detect_ticker_prefix_only_spam()`, `detect_repeated_skeleton_spam()`, `detect_weak_buy_rationale()`.
- `_clean_evidence_text()` sanitizes LLM-generated driver text before embedding in rationale (removes raw metric keys, price targets, truncates).
- 34 new backend tests in `test_v3_evidence_rationale.py`; 572 existing v3 tests still pass.
- No Supabase SQL. No frontend changes. No new providers. No LLM calls.

Production validation checklist after deploy:
  1. `repeated_skeleton_count=0` in Railway cert log
  2. `ticker_prefix_only_reason_count=0` in Railway cert log
  3. `weak_buy_rationale_count=0` in Railway cert log
  4. BUY card texts are distinct and evidence-grounded
  5. HOLD card texts explain WHY not adding

## 2026-05-06 — Intel v3 Visible-Path Certification Fix (Level 2)

- Fixed `generic_copy_count=29` root cause: `_build_rationale()` in `decision_policy_v1.py` now includes ticker in every rationale string → all 34 cards produce unique `why_text` → `generic_copy_count=0`.
- Added `intel_v3_snapshot_certification_summary` log to `intel_v3_service.py` with full certification fields readable from Railway after one production run.
- Fixed legacy hooks firing during v3 page load: added `legacyEnabled = !INTEL_V3_ENABLED` in `page.tsx`; `useRecommendations`, `useLatestAgentRun`, `useDecisionLog` all receive `enabled=false` when v3 flag is active.
- Added `enabled` param to `useRecommendations` hook in `hooks.ts`.
- 21 new backend tests (`test_v3_certification_fix.py`) + 13 new frontend tests (`IntelV3Contract.test.ts`).
- No new Supabase SQL. No new providers. No LLM calls.

## 2026-05-06 — Intel v3 Pre-merge Hardening Pass (PR #215 blockers)

- Fixed 8 pre-merge blockers: import path, migration file, fail-closed validator, service/router tests, legacy bridge comment, frontend tests, docs accuracy.
- `intel_v3_service.py`: fixed `from ..recommendation_engine` → `from ...recommendation_engine`. Added fail-closed: raises ValueError + skips persist when hard violations exist.
- `source_validator_lite.py`: `HARD_VIOLATION_RULES` frozenset, `hard_violation_count` property, 3-tuple return from `validate_snapshot_cards`.
- `v2/database/016_intel_v3_snapshots.sql`: migration file now in repo.
- `test_intel_v3_router_service.py`: 24 new tests — app import, flag behavior, snapshot contract, fail-closed, page-load isolation, run path.
- `IntelV3Contract.test.ts`: 18 new frontend contract tests.
- Total: 78 backend + 18 frontend v3 tests pass.

## 2026-05-06 — Intel v3 Snapshot Spine + Premium Cockpit UI (Level 3 Rebuild)

- Built the v3 held-position intelligence spine end-to-end: decision kernel (K1) → portfolio governor lite (K2) → snapshot store (K3) → premium cockpit UI (K4) → source validator lite (K5).
- Fixed production failure classes: HOLD-collapse (independent axis decision policy), action_counts divergence (derived from card actions only), run ID divergence (one snapshot_id per run), page-load LLM calls (snapshot read is zero LLM calls).
- New Supabase table: `intel_v3_snapshots` (additive, RLS-gated).
- New endpoints: `GET /api/v1/intel/v3/snapshot`, `POST /api/v1/intel/v3/run`, `GET /api/v1/intel/v3/runs/{run_id}`.
- New frontend components: `IntelV3Cockpit`, `IntelV3Card`, `IntelV3Drawer` — read ONLY from v3 snapshot, never from legacy cards.
- Feature flag: `INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` / `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` — binary gate, no blending.
- 54 new regression tests (18 decision policy + 36 snapshot/integration) — all pass.
- Legacy path untouched: flag=false reverts page to legacy with no code changes.

## 2026-05-06 — Runtime certification server-to-server auth hardening

- Added diagnostics-only server-to-server auth path for `POST /api/v1/diagnostics/finance-intel/certify`: still env-gated (`FINANCE_RUNTIME_CERT_ENABLED`) and secret-gated (`X-Finance-Runtime-Cert-Secret`), now supports non-Bearer callers via configured cert identity (`FINANCE_RUNTIME_CERT_USER_ID`, optional `FINANCE_RUNTIME_CERT_USER_EMAIL`).
- Returns safe 403 when cert user env is missing/invalid; normal JWT auth still required everywhere else.
- Added focused tests for disabled, bad secret, missing cert user, read-only summary, and force/nonforce propagation.

## 2026-05-05 — Intel Card Narrative Contract v1: Full Evidence Check action-consistency fix (Level 3 / Sev 1)

- Root cause (two-part, both fixed):
  1. `_derive_intel_posture` rule 5.5: `BUY + insufficient_data → "Review"` posture_label → `build_posture_reason("Review")` → "Reviewing before taking action — the setup is interesting but not yet complete." appeared as the PRIMARY Evidence Check text on BUY cards.
  2. `_build_caveat` WATCH-posture fallback: `r2.action.posture=WATCH + n_trusted>=1` → "Treat this as an early signal, not a complete picture." appeared as secondary Evidence Check text on BUY cards.
- Fix:
  - Rule 5.5 removed: `BUY action → Add Candidate` regardless of `insufficient_data`. BUY cards now always route to "Add Candidate" posture bucket.
  - Added `build_intel_card_narrative_contract()` in `reasoning_v2_plain_english.py`: single deterministic helper keyed on the VISIBLE action that produces action-consistent `evidence_summary` and `final_takeaway`. Replaces fragmented `build_posture_reason + _build_caveat` for the Evidence Check voice.
  - Added `detect_intel_card_conflict()`: pure function that detects forbidden HOLD phrases on BUY cards and forbidden BUY phrases on TRIM/SELL cards. Used before contract is applied to count pre-fix conflicts.
  - `recommendation_engine.py` card assembly: calls narrative contract after gate code, overrides `intel_read_dict["posture_reason"]` and `intel_read_dict["caveat"]` with contract values, stores `intel_read_dict["narrative_contract"]` for observability.
  - Observability: emits `intel_card_narrative_contract_summary` INFO log per run; WARNING when conflict_count > 0.
  - `api.ts`: added `narrative_contract` optional field to `IntelRead` interface.
- Scope/guardrails: no all-HOLD re-fix, no evidence-quality remap, no SQL, no LLM, no Deploy, no frontend redesign.
- Tests: 67 new tests in `test_v3_intel_card_narrative_contract.py` — all pass. 257 total tests pass.

## 2026-05-05 — Intel v3 PR 13: Sev 1 all-HOLD Intel collapse fix (Level 3)

- Root cause: `intel_read.insufficient_data=True` (set when scorecard.status=INSUFFICIENT_DATA due to missing growth/risk axes) was used as a binary global HOLD gate in three places. This collapsed ALL 34 cards to HOLD even when quality/valuation/momentum axes were published and analyst verdict said BUY. The `insufficient` flag dominated over the trusted_signals count in `classify_evidence_signals()` and `_derive_evidence_quality()`, and the card assembly gate forced BUY→HOLD for any card with insufficient_data=True regardless of trusted signal count. This is why PR 12 key fix (trusted_dimensions→trusted_signals) did not fix production: the `insufficient` flag still overrode the trusted count.
- Fix (Option B — v2 gate fix; v3 shadow not yet visible): Changed all three `if insufficient or n_trusted == 0:` gates to `if n_trusted == 0:`. Missing one axis no longer collapses the entire card — only zero trusted signals triggers HOLD.
  - `recommendation_engine.py` visible gate: n_trusted==0 → global collapse; n_trusted>=1 → preserve action, downgrade conviction only.
  - `existing_signal_adapter._derive_evidence_quality`: n_trusted==0 → THIN; n_trusted>=1 → OK/STRONG.
  - `data_truth_v1.classify_evidence_signals`: n_trusted==0 → WEAK; n_trusted>=1 → PRESENT/MEDIUM or PRESENT/HIGH.
- Added `tests/test_v3_intel_collapse_fix.py` — 30 production-shaped tests covering: partial-coverage PRESENT in data_truth, OK/STRONG in adapter, shadow BUY emergence, 34-card portfolio no longer all WEAK, mixed portfolio action diversity regression, TRIM/SELL risk protection invariants, conviction ladder.
- No SQL, no API schema changes, no frontend redesign, no Deploy, no provider, no LLM. No new env flags needed for the fix (existing v2 gate is now correct).
- Tests: 372 v3 tests pass (342 existing + 30 new).

## 2026-05-05 — Intel v3 PR 12: evidence-quality source mapping calibration (Level 2)

- Root cause: `classify_evidence_signals()` and `_derive_evidence_quality()` read `intel_read.get("trusted_dimensions")` but production intel_read (built by `build_intel_read()`) uses `"trusted_signals"`. n_trusted was always 0 → all 34 cards returned WEAK/LOW. Test helpers also used the wrong key, so tests passed but concealed the production mismatch.
- Fix: Changed `trusted_dimensions` → `trusted_signals` in `data_truth_v1.py` and `existing_signal_adapter.py`. Added `analyst_used_fallback` parameter through the 5-level call chain (`classify_evidence_signals` → `evaluate_card_signals_truth` → `build_truth_aware_decision_input` → `project_shadow_from_card_signals` → `_v3_shadow_projection`). analyst_used_fallback=True conservatively caps PRESENT/HIGH → PRESENT/MEDIUM. Updated 6 test files' fixtures to use `trusted_signals`.
- Added `tests/test_v3_evidence_quality_source_mapping.py` — 37 production-shaped tests covering: field-name fix verification, fallback cap boundaries, data_quality_label path, shadow projection shapes, mixed portfolio non-uniformity, 34-card regression.
- Evidence-quality contract: PRESENT/HIGH requires ≥3 trusted_signals + non-fallback. PRESENT/MEDIUM for 1-2 signals or fallback + 3 signals or data_quality_label=MEDIUM. WEAK/LOW for insufficient/empty. MISSING for absent fields.
- No SQL, no API schema, no frontend, no Deploy, no provider, no LLM changes. No visible threshold tuning.
- Tests: 341 v3 tests passed (304 existing + 37 new).

## 2026-05-05 — Intel v3 PR 11: v3 truth diagnostics wiring fix / signal hydration audit (Level 2)

- Root cause: `_v3_shadow_projection(card)` in `recommendation_engine.py` was stale — called `_v3_shadow_decide()` → non-truth-aware `build_decision_input_from_card()` and returned dicts without `truth_diagnostics`. All truth/guardrail summary functions looked for `truth_diagnostics` per card and found nothing → safe_axis_count=0, evidence_quality_status_counts={}, guardrail_evaluated_count=0 in production.
- Fix: replaced `_v3_shadow_decide` + old `_v3_shadow_projection` body in `recommendation_engine.py` with a thin delegate to `project_shadow_from_card_signals()` (the truth-aware path added in PR 7 but never wired here).
- Added `tests/test_v3_signal_hydration.py` — 25 production-shaped synthetic fixture tests covering truth_diagnostics hydration, evidence_quality_status_counts non-empty, 34-card HOLD batch regression, fail-soft, and visible action invariants.
- No SQL, no API schema, no frontend, no Deploy, no provider, no LLM changes. No threshold tuning.
- Tests: 400 passed (375 existing + 25 new).

## 2026-05-05 — Intel v3 PR 9: shadow-only evidence-quality BUY conviction guardrail (Level 2)

- Added `backend/app/services/intelligence/v3/buy_conviction_guardrail.py` — pure function `apply_buy_conviction_guardrail()` that caps HIGH-conviction BUY to MEDIUM when evidence-quality truth is not PRESENT/HIGH-trust.
- Wired guardrail into `backend/app/services/intelligence/v3/shadow_projection.py` post-decide; result: `v3_shadow_conviction` reflects post-guardrail value; `truth_diagnostics.buy_conviction_guardrail` sub-dict added.
- SELL/TRIM protective decisions unaffected. BUY preserved at MEDIUM when guardrail fires. v2 visible actions never mutated.
- Added 57-test suite `backend/tests/test_v3_evidence_quality_guardrail.py` (unit + integration + golden portfolio regression). 276 total v3 tests pass.
- No SQL, no API schema, no frontend, no Deploy, no provider, no LLM changes.

## 2026-05-05 — Intel v3 PR 8: optional INFO-level truth-aware shadow suppression summary logs (Level 1)

- Reused existing env gate `INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED` from PR 4; default behavior remains unchanged (DEBUG summary always, INFO summary only when enabled).
- Added `summarize_truth_aware_suppression()` in `backend/app/services/intelligence/v3/shadow_projection.py` to aggregate `safe_axis_count`, `unsafe_axis_count`, `suppressed_axis_reasons` (reason-code counts), and `dominant_truth_reason` across cards.
- Added `_build_v3_shadow_info_summary()` in `backend/app/services/recommendation_engine.py` to merge PR 3/4 shadow summary keys with PR 7 truth-aware suppression aggregates and emit one INFO summary per batch through existing logging helper.
- Extended `backend/tests/test_recommendation_engine.py` with payload contract checks and truth-aware aggregate assertions.
- No UI/API/Deploy/SQL/provider/LLM/policy-gating changes; no sensitive/raw payloads added.
- Tests: 315 passed (`test_recommendation_engine`, `test_v3_shadow_projection`, `test_v3_truth_aware_adapter`, `test_v3_data_truth`, `test_v3_decision_policy`).

---

---

## 2026-05-05 — Intel v3 PR 7: truth-aware v3 shadow input adapter (Level 2)

- Added `build_truth_aware_decision_input()` to `existing_signal_adapter.py`: calls `evaluate_card_signals_truth()` first; axes with `safe_for_decision=False` (MISSING, UNAVAILABLE, CONFLICTING, STALE) have their input signals nulled before `build_decision_input_from_card()`; WEAK axes pass through (LOW trust, safe per PR 6).
- Updated `shadow_projection.py`: uses new truth-aware function; `truth_diagnostics` extended with 5 additive keys (`truth_aware_adapter_enabled`, `safe_axis_count`, `unsafe_axis_count`, `suppressed_axis_reasons`, `dominant_truth_reason`); all PR 2/3 stable keys unchanged.
- 68 new backend tests in `test_v3_truth_aware_adapter.py`; 219 total tests pass.
- No SQL, no UI, no API schema, no Deploy, no provider expansion, no LLM calls, no real user data. v3 shadow action may change for conflicting/missing signal cards (intended dark-launch behavior).

---

## 2026-05-05 — Intel v3 PR 6: backend-only Data Truth Contract v1 (Level 2)

- Added `data_truth_contracts.py`: DataTruthStatus (PRESENT/MISSING/STALE/WEAK/CONFLICTING/UNAVAILABLE) and SourceTrustLevel (HIGH/MEDIUM/LOW/UNKNOWN) enums; DataTruthFinding and AxisTruthSummary dataclasses.
- Added `data_truth_v1.py`: pure classifiers for evidence, action (with BUY↔SELL conflict detection), conviction, technical, and risk signal groups; `classify_with_staleness` for future timestamp-aware freshness.
- Added `existing_signal_truth_adapter.py`: `evaluate_card_signals_truth` builds one AxisTruthSummary per axis from existing card fields; `build_truth_diagnostic_summary` produces stable compact dict for shadow logging.
- Shadow projection (`shadow_projection.py`): additive `truth_diagnostics` key attached to per-card diagnostic dict; existing stable keys unchanged.
- 85 new backend tests in `test_v3_data_truth.py`; 151 total tests pass.
- No SQL, no UI, no API schema, no Deploy, no provider expansion, no LLM calls, no real user data.

---

## 2026-05-05 — Intel v3 PR 5: backend shadow golden-portfolio validation suite (Level 1)

- Added `TestV3ShadowGoldenPortfolio` to `backend/tests/test_v3_shadow_projection.py` using synthetic held-portfolio fixtures only.
- Reused existing `project_shadow_from_card_signals(...)` and `summarize_shadow_diagnostics(...)` helpers to validate end-to-end shadow diagnostics.
- Added coverage for: action diversity (BUY/HOLD/TRIM/SELL), HOLD-collapse detection, honest HOLD separation, fail-soft projection failures, deterministic stable summary schema/counts, and visible v2 action immutability.
- Confirmed no Deploy/API/UI/SQL/provider/LLM/persistence changes.

### feat: install code-review-graph for Claude Code platform
- **Commit**: `abfc393`
- **Date**: April 13, 2026

Installed a lightweight code-review-graph pipeline wired into Claude Code's `.claude/settings.json` hooks:

- `scripts/build_code_graph.py`: Pure-Python AST walker that scans all `.py` files, builds an import dependency graph, and writes `graphify-out/GRAPH_REPORT.md` (god nodes, communities, edge list) and `graphify-out/wiki/index.md` (per-module stub wiki).
- `scripts/rebuild_graph_on_edit.sh`: Thin hook wrapper that reads stdin JSON and rebuilds the graph only when edited file ends in `.py`.
- `.claude/settings.json` updated with three new hooks:
  - `SessionStart` → builds graph at session start (async, non-blocking)
  - `PostToolUse Write|Edit` → rebuilds graph on `.py` edits (async)
  - `PreToolUse Bash(git commit*)` → existing agent hook extended with Step 1b: loads `GRAPH_REPORT.md` and surfaces god-node blast radius in code review
- `graphify-out/GRAPH_REPORT.md` and `graphify-out/wiki/index.md` generated (initial run: 79 modules, 22 god nodes, 15 communities).

**Code review:** No issues. **Security review:** No issues.

---

### Integrate multi-agent trading reasoning engine
- **Commit**: `9564c8c`
- **Date**: April 13, 2026
Replaces the rule-based recommendation refresh with a TradingAgents-style
pipeline (Sentiment → Technical → Fundamental → Portfolio Manager) running
as a FastAPI BackgroundTask with Supabase-tracked progress.

**Backend (`v2/backend/app/services/agents/`)**
- Hand-rolled async orchestrator with `asyncio.Semaphore(6)` bounded concurrency
- Per-agent Claude Sonnet (claude-sonnet-4-6) prompts with deterministic pre-scoring
- Portfolio Manager blends conviction (Fund 0.50 / Tech 0.30 / Sent 0.20) with
  concentration penalty (10% soft cap / 20% hard cap) and proportional cash allocation
- Data sources: Finnhub + yfinance news (sentiment), yfinance history + Polygon aggs
  (technicals), yfinance fundamentals / CoinGecko (fundamentals + crypto)

**Router / API**
- `POST /recommendations/refresh` → 202 `{job_id}` via FastAPI BackgroundTasks
- `GET /recommendations/jobs/{id}` → live AgentRunStatus polling
- `GET /recommendations/jobs/{id}/insights` → per-ticker agent insights
- `GET /recommendations/insights/latest` → latest completed run insights

**Database (`v2/database/002_agent_insights.sql`)**
- New `agent_runs` table (status, current_agent, progress_pct, allocation JSONB)
- New `agent_insights` table (investment_thesis, sentiment/technical/fundamental scores,
  conviction_score, suggested_allocation per ticker)
- `recommendations` enriched with thesis, sentiment, technical, conviction, allocation

**Frontend**
- `AgentInsightCard`: investment thesis, sentiment label + score, conviction bar (−1..+1),
  allocation pill, P&L pill, tax note
- `AgentProgressTracker`: live 5-step pipeline (Loading → Sentiment → Technicals →
  Fundamentals → Portfolio Mgr) driven by `current_agent` regex, progress bar, summary
- Recommendations page polls `useAgentJob(jobId)` every 1.5s; auto-clears 4s after
  completion; swapped InsightCard → AgentInsightCard

**Files**
- New: `agents/__init__, data_sources, llm, state, sentiment_agent, technical_agent,
  fundamental_agent, portfolio_manager, orchestrator, job_runner`
- New: `database/002_agent_insights.sql`
- New: `frontend/src/components/cards/AgentInsightCard.tsx`
- New: `frontend/src/components/cards/AgentProgressTracker.tsx`
- Modified: `models/recommendation.py`, `routers/recommendations.py`,
  `services/recommendation_engine.py`, `frontend/src/lib/api.ts`,
  `frontend/src/lib/hooks.ts`, `frontend/src/app/dashboard/recommendations/page.tsx`

https://claude.ai/code/session_01NZTEYaJy3iF3Vzxpqp6t4x

### Ignore tsconfig.tsbuildinfo (TypeScript incremental build artifact)
- **Commit**: `0034696`
- **Date**: April 13, 2026
https://claude.ai/code/session_01NZTEYaJy3iF3Vzxpqp6t4x

### fix: repair pre/post-commit hooks and Claude Code hook guards
- **Commit**: `4bf42d7`
- **Date**: April 12, 2026
Git hooks (.githooks/):
- Register core.hooksPath via git config (hooks were never running)
- pre-commit: tighten secrets check to match actual value assignments
  (api_key = "long_value") instead of just the word "secret/token",
  eliminating false positives on API management code
- post-commit: add .git/POST_COMMIT_RUNNING flag file guard to prevent
  infinite amend loop (amend was re-triggering post-commit forever)

Claude Code hooks (.claude/settings.json):
- PreToolUse: add skip guard — exits immediately if commit message
  contains 'Auto-update docs' or only docs files are staged, preventing
  recursive code review on the docs-update commit
- PreToolUse: replace hardcoded date "April 9, 2026" with dynamic
  `date '+%B %d, %Y'` shell call
- PostToolUse: add skip guard — exits if HEAD is already 'Auto-update
  docs' (breaks the push→hook→push→hook infinite loop)
- PostToolUse: now commits AND pushes the docs update (previously
  committed locally only, leaving an orphaned unpushed commit after
  every push)

https://claude.ai/code/session_016tyJuMoVnVHK7EC5gHiFkZ

### Fix: Deploy tab calculates deposit allocations without requiring manual target setup
- **Commit**: `b586d8a` (PR #19)
- **Date**: April 12, 2026
- `calculate_rebalance()` now falls back to built-in deposit formula (NVDA 28% / VOO 22% / VYM 17% / QQQ 17% / ROTATING 16%) when no user-defined targets exist in the DB — eliminates the "No target allocations set" error
- ROTATING slot is auto-resolved to the highest-urgency active Intel BUY recommendation not already in the formula; falls back to a placeholder with guidance if no qualifying signal found
- In deposit mode, `suggested_amount` is the direct formula split of cash to deploy (e.g. $900 × 28% = $252 for NVDA)
- Results enriched with Intel action/urgency badges and DRIP yield notes sourced from active recommendations and yield map
- `RebalanceResult` backend model gains 5 optional enrichment fields; TypeScript interface updated to match
- Deploy page shows a formula-mode banner, dynamic section heading ("Deposit Allocation — $900"), `IntelBadge` component, rationale text, and DRIP note per row
- **Files**: `v2/backend/app/models/portfolio.py`, `v2/backend/app/services/portfolio_service.py`, `v2/frontend/src/app/dashboard/deposits/page.tsx`, `v2/frontend/src/lib/api.ts`

---

### Add git hooks: pre-commit validation and post-commit doc auto-update
- **Commit**: `31d3e3f`
- **Date**: April 11, 2026
Setup:
- Configure git to use .githooks directory (core.hooksPath)
- .githooks/pre-commit: validates Python syntax, JSON, detects secrets,
  prevents large files (>10MB), checks for trailing whitespace
- .githooks/post-commit: auto-updates v2/progress_log.md with latest
  commit info, stages and amends if docs changed
- scripts/update_docs.py: parses git log, appends Recent Changes section

Hooks run automatically on every commit/push. Progress log now tracks all
changes without manual intervention.

https://claude.ai/code/session_01PpLvPsnx3T9uMW7igCZnBr

### Update documentation: reflect Phase 2-4 completion status
- **Commit**: `9c8fe9b`
- **Date**: April 11, 2026
- Progress log: Add v2.1.0 entry documenting cache invalidation fix and comprehensive Plaid test coverage (32 tests)
- README: Mark Phase 2-4 items complete (Plaid, yfinance, Alpaca, AI recommendations, DRIP, settings)
- Update roadmap to reflect current implementation status

https://claude.ai/code/session_01PpLvPsnx3T9uMW7igCZnBr

---

---

## v2.1.0 — Bug Fixes & Test Coverage (April 11, 2026)

### Fixed: API Key Configuration Badges Not Refreshing
- **Issue**: After saving API keys (Plaid, Finnhub, Alpaca, Anthropic) in Settings, the "Configured" badges remained stale until page reload
- **Root Cause**: `useUpdateApiKeys()` hook in `hooks.ts` was not invalidating the `["auth", "me"]` query cache after `PUT /api/v1/auth/me/api-keys` succeeded
- **Fix**: Added `onSuccess: () => qc.invalidateQueries({ queryKey: ["auth", "me"] })` to `useUpdateApiKeys()` mutation
- **Impact**: Badges now update immediately after saving (same pattern as `useUpdateProfile`)

### Test Coverage: Comprehensive Plaid Service Tests
- **Expanded**: `test_plaid_service.py` from 9 to **32 unit tests** (all passing)
- **New `TestCallPlaid` class** (15 tests):
  - Success path: 2 holdings, per-share cost basis calculation, cash summing across accounts
  - URL routing: sandbox → `sandbox.plaid.com`, production/development → `production.plaid.com`
  - Error handling: Non-2xx response raises `RuntimeError` with Plaid's `error_message`
  - Edge cases: `None` quantity/cost/price/balance → defaults to 0.0 (no crashes)
  - Filtering: `CUR:USD` cash holdings skipped, no-ticker holdings skipped
  - Normalization: `BRK.B` → `BRK-B`, `BF.A` → `BF-A`
  - Request structure: Verifies JSON body has `access_token`, `client_id`, `secret`
  - Crypto: `security_type` preserved as "cryptocurrency"
  - Multi-account: Cash from multiple accounts summed correctly
  - Missing/None keys: Account without `balances` key doesn't crash
- **New `TestSyncHoldings` class** (5 tests):
  - Cache hit: Fresh sync returns cached result without calling Plaid API
  - Cache miss: Never-synced or stale sync triggers API call
  - Force flag: `force=True` bypasses cache
  - Credentials: Missing `encrypted_plaid_access_token` returns error
  - API errors: Plaid failures logged and returned as `error` SyncResult

### Removed Dependency
- Removed `plaid-python>=22.0.0` from `requirements.txt` — v2 backend now uses direct httpx POST calls to `/investments/holdings/get` instead of the plaid-python SDK
  - Resolves the pydantic v2 composed-schema validation error: "Values stored for property balances in InvestmentAccount differ..."
  - httpx approach gives full control over None-safety for Robinhood's quirky response shapes

### Files Modified
- `v2/frontend/src/lib/hooks.ts`: +2 lines (cache invalidation in `useUpdateApiKeys`)
- `v2/backend/tests/test_plaid_service.py`: +588 lines (expanded test suite with docstrings)
- `v2/backend/requirements.txt`: -1 line (removed `plaid-python`)

---

## v2.0.0 — Phase 1: Database & Architecture Setup (April 8, 2026)

> Archived to `v2/progress_log_archive.md`.

### Decision Log Performance v1 (April 30, 2026)
- Added windowed decision performance evaluation outputs (`7d`, `30d`, `90d`) with honest statuses (`pending`, `ready`, `insufficient_data`, `unavailable`) instead of defaulting to misleading zeros.
- Kept existing Deploy logging flow and allocation/recommendation logic intact; extended stored `performance_snapshot` payload only.
- Added backend tests for window readiness/pending behavior and unavailable-data handling.
- Added minimal Deploy Step 3 UI lines to show 7d/30d/90d status/returns when available.

### Step 3 Execute & Record durability fixes (April 30, 2026)
- Fixed Step 3 confirm flow to persist execution immediately (create/update decision log with actual decisions) instead of only setting local UI text.
- Added guarded success/error messaging so UI only shows save success after backend mutation resolves.
- Rehydrated latest saved decision log on load from decision log history query to survive refresh.
- Updated Modify Plan action to open actual execution editor directly.
- Moved confirm execution modal rendering to a portal with higher z-index to resolve layering/clickability issues.

### Step 3 idempotency guardrails for Decision Logs (April 30, 2026)
- Root cause found: repeated Step 3 confirm/save calls could hit create path again when `savedLog` was unset for the current render lifecycle, and frontend rehydration picked latest log without verifying it belonged to the active recommendation session.
- Added deterministic `decision_context.session_key` to recommendation snapshots so a Step 2 recommendation maps to one Step 3 log candidate.
- Updated Step 3 handlers to prefer update over create when a matching recent log exists for the same session key.
- Rehydration now binds `savedLog` to the matching session log instead of blindly taking `recentLogs[0]`.
- Added frontend unit test coverage for deterministic session key generation.
- No Supabase SQL required; this is implemented in frontend flow/state guardrails.
- Known limitation: already-created duplicate rows remain in DB history and are not auto-deleted.

### Step 3 durable recommendation-key idempotency + deploy percentage semantics (April 30, 2026)
- Root cause: duplicate logs still appeared when create endpoint was called again after refresh/tab lifecycle before frontend state rehydration, and backend always inserted new rows without checking recommendation identity.
- Added stable `decision_context.recommendation_key` (kept `session_key` for backward compatibility) derived from entered capital, deploy-now, reserve, and sorted ticker allocations.
- Backend `DecisionLogService.create` now enforces idempotent create/update behavior by checking for an existing row with the same recommendation key in `recommendation_snapshot.decision_context` and updating that row instead of inserting a duplicate.
- Kept allocation/recommendation engine unchanged; this only affects decision-log identity and persistence behavior.
- Updated Step 3 and Recent Decision Logs copy to separate plan execution % from total deposit deployed % (e.g., "$715 of $715 plan (100%) · 79% of $900 deposit").
- Added/updated frontend unit tests for deterministic key generation and changed-key behavior when deposit context changes.
- No Supabase SQL required.
- Known limitation remains: historical duplicate rows already stored are not auto-deduplicated.
## 2026-05-01 — Intel v2 PR-5: backend-only cash-flow quality coverage (safe fcf_margin)

- Added additive yfinance provider fields to fundamentals payload:
  - `free_cash_flow` (`info.freeCashflow`)
  - `operating_cash_flow` (`info.operatingCashflow`)
  - `revenue` (`info.totalRevenue`)
- Added safe mapper derivation:
  - `fcf_margin = free_cash_flow / revenue` only when both numeric and `revenue > 0`.
  - Omit on missing/invalid/NaN/`revenue <= 0`.
- Explicitly preserved no-proxy guardrails:
  - `profit_margin` not mapped to `fcf_margin`.
  - Existing semantic guardrails for ROE→ROIC, D/E→NDE, earnings_growth→forward_revenue_growth_est unchanged.
- Added focused mapper tests for:
  - exact fcf_margin math
  - omission when FCF missing
  - omission when revenue missing
  - omission when revenue <= 0
  - no proxy mapping from profit_margin
- Validation:
  - `cd v2/backend && pytest -q tests/test_thesis_mapper.py tests/test_thesis_engine.py` (116 passed)
- No Supabase SQL. No frontend/UI changes. No Deploy or LLM behavior changes.

- Intel v2 PR-7: added backend-only deterministic plain-English thesis translation module + focused tests; no UI/Deploy/LLM changes.
## 2026-05-02 — Deploy UI: allocation table why moved under ticker

- Removed separate WHY column from Deploy Allocation Breakdown in `v2/frontend/src/app/dashboard/deposits/page.tsx`.
- Ticker cell now shows symbol + why text inline, with fallback to existing staging/execution subtitle only when why is absent.
- Kept role/invest-now/now%/after% columns and all allocation math unchanged.
- Validation: lint passed; build requires Supabase public env vars in this environment; targeted deploy test command unavailable due to missing `jest` binary.
- No backend changes. No Supabase SQL.

## 2026-05-02 — Intel UI: clarify run vs ticker data-quality labels

- Clarified top Intel quality chips to read `Run data {HIGH|MEDIUM|LOW}` (run/portfolio aggregate context).
- Clarified per-card quality chip to read `Ticker data: {HIGH|MEDIUM|LOW}` (ticker-level context).
- Kept styling/layout unchanged; copy-only update for mobile-safe compact labels.
- No backend/scoring/data-quality logic changes. No Supabase SQL.

## 2026-05-02 — Intel v2: thesis_plain_english card coverage reliability fix

- Root cause: strict exact ticker-key lookup into `agent_runs.allocation["_thesis_v2"]` dropped valid scorecards when symbol formats differed (case, dot/dash/space variants).
- Added backend-only tolerant lookup normalization for thesis scorecard retrieval:
  - exact key match first
  - fallback normalized key match (uppercase alphanumeric-only).
- Preserved safe behavior: when `_thesis_v2` missing/malformed or no ticker match, thesis fields are omitted without breaking card responses.
- Added focused recommendation_engine tests for normalization and malformed/missing map handling.
- Validation: `cd v2/backend && pytest tests/test_thesis_response_wiring.py tests/test_thesis_plain_english.py tests/test_recommendation_engine.py -k thesis -q`
- No Supabase SQL. No frontend/UI changes. No scoring/Deploy/LLM behavior changes.
- 2026-05-02: fix(intel): replaced user-facing "thesis" wording with plain-English "investment case"/"business case" copy in Intel backend/frontend templates; no logic changes.

## 2026-05-02 — Intel v2 copy cleanup: remove remaining user-facing “thesis” jargon

- Updated Intel analyst prompt copy to replace user-facing phrasing `breaks thesis` with `breaks the business case` in the risk field guidance.
- Updated portfolio synthesis prompt rules to explicitly require plain wording and avoid `thesis` jargon in user-facing lines.
- Scope limited to v2 copy/template text only; no scoring, recommendation, deploy, SQL, or LLM-call wiring changes.
- Validation: `python -m compileall v2/backend/app/services/intelligence/per_ticker_analyst.py v2/backend/app/services/intelligence/portfolio_synthesis.py`.

## 2026-05-02 — Intel v2 thesis_plain_english coverage diagnostics hardening

- Investigated live Intel card thesis coverage path across orchestrator write path and recommendation card read path.
- Confirmed `_thesis_v2` write path exists in orchestrator completion allocation payload; patch focused on card read-path reliability/observability.
- Added deterministic helper to resolve + translate thesis fields per card with explicit diagnostic outcomes (`attached`, `run_not_found`, `thesis_map_missing`, etc.).
- Added focused backend tests for exact-key attach, safe normalized-key attach, and missing-map omission behavior.
- No score math, LLM behavior, Deploy, SQL, or frontend changes.


- 2026-05-02: Added Intel live-contract diagnostic test for live-style serialized `_thesis_v2` (GOOGL/META/NVDA) to verify backend no longer emits universal INSUFFICIENT_DATA dimension fallback when published dimensions exist.

## 2026-05-02 — Intel reasoning_v2: actionable INSUFFICIENT_DATA diagnostics fix

- Root cause: reasoning_v2 diagnostics read missing_fields/stale_fields, but live thesis_v2 serialization provides inputs_missing instead; this produced INSUFFICIENT_DATA with missing=[] and stale=[].
- Fix: fallback to inputs_missing and emit suppressed dimension markers (suppressed:<dimension>) for INSUFFICIENT_DATA when dimensions are unpublished and missing/stale would otherwise be empty.
- Preserved safety contract: INSUFFICIENT_DATA still forces WATCH and deterministic evidence remains empty.
- Added focused reasoning_v2 builder regression tests for the live-style serialized shape and suppression diagnostics.
- No Supabase SQL. No frontend/UI, Deploy, or LLM behavior changes.

## 2026-05-04
- Intel card rendering hardening: frontend now maps LOW conviction badge copy to "Evidence limited" (no literal "LOW CONVICTION"), collapses duplicate category/subcategory labels (e.g., Core · Core -> Core), and shows WATCHLIST action badge text for insufficient-data HOLD cards so top badge aligns with conservative watchlist action copy.
- WHY THIS VIEW bottom_line wording for insufficient-data cards now references available/missing evidence directly (e.g., "Evidence is strongest on ... but ... still missing") instead of repeating the generic "Interesting setup..." sentence.
- Added focused tests for new rendering contract helpers and bottom_line anti-generic behavior; backend suites pass.
- No Deploy changes, no allocation math changes, no SQL/migrations.

## 2026-05-05 — Intel v3 PR 3 (backend shadow summary)
- Added backend-only portfolio-level v3 shadow diagnostic summary aggregation/logging after card assembly.
- Kept per-card shadow diagnostics intact and fail-soft.
- Added focused backend tests for summary aggregation/counting and failure handling.
- No UI/API/Deploy/SQL/provider/LLM changes.

- 2026-05-05: Intel v3 PR 10 merged scope prepared — added backend-only portfolio/batch guardrail-impact observability aggregation for PR 9, wired into existing env-gated v3 shadow INFO summary path (default off), and added focused tests; no visible/UI/API/Deploy/SQL/provider/LLM changes.

### 2026-05-06 — Runtime certification harness (infra unblocker post-PR209)
- Added backend-only diagnostics endpoint: `POST /api/v1/diagnostics/finance-intel/certify`.
- Env-gated protection added:
  - `FINANCE_RUNTIME_CERT_ENABLED` (default false)
  - `FINANCE_RUNTIME_CERT_SECRET` (required if enabled)
  - request header `X-Finance-Runtime-Cert-Secret`
  - disabled => 404, invalid/missing secret => 403.
- Added modes:
  - `read_only_cards` (page-load card assembly path, no new LLM calls)
  - `force_run_agents` (queues existing refresh pipeline with `force=true`)
  - `nonforced_run_agents` (queues existing refresh pipeline with `force=false`)
- Added certification response/log payload `finance_intel_runtime_certification` including pass/fail/inconclusive status + failure reasons.
- Added unit tests for gating, secret validation, read-only summary output, force/nonforce propagation, and status logic.
## 2026-05-06 — Runtime certification diagnostics polling unblock (Level 2)

- Root cause: diagnostics certify API returned poll URLs for `/api/v1/recommendations/jobs/{job_id}`, but those endpoints require normal Bearer auth. GitHub certification flow only sends `X-Finance-Runtime-Cert-Secret`, so polling failed.
- Added diagnostics-only polling endpoints:
  - `GET /api/v1/diagnostics/finance-intel/jobs/{job_id}` → `RecommendationService(user_id=cert_user.id).get_job_status(job_id)`
  - `GET /api/v1/diagnostics/finance-intel/jobs/{job_id}/insights` → `RecommendationService(user_id=cert_user.id).get_agent_insights(run_id=job_id)`
- Updated diagnostics certify response poll paths to diagnostics routes for both force and nonforced modes.
- Recommendations polling endpoint auth remains unchanged.
- Added focused runtime certification tests for cert polling auth behavior, 403/404 guards, poll path correctness, and unchanged recommendations auth boundary.

## 2026-05-06 — Post-PR-221 fallback rationale de-duplication
- Noted prior sequence: PR #220 decoupled legacy path; PR #221 fixed weight-map source.
- Fixed remaining deterministic high-risk/speculative HOLD fallback sentence reuse that triggered `ticker_prefix_only_reason_count` and `repeated_skeleton_count`.
- Added certification example expansion to log up to 5 safe examples (ticker + first 120 chars of why_text).
