# Build Queue

The active product queue. Every meaningful implementation PR should map to one item here.

Update via `.claude/skills/build-queue-update/SKILL.md` after meaningful roadmap decisions or merged PRs. Keep updates concise.

## Now (active)

- **Stage 5E** — Truth adapter. Consumes Stage 5D completeness assessment to determine whether artifact claims are usable as structured evidence inputs for Intel v3 decision support.

## Later (Stage 5 backend)
- Stage 5E — Truth adapter (see Now).
- Stage 5F – Stage 5L — Finance research workers (filings, sentiment, technical, fundamental, company strategy, pattern detection, Radar candidates).
- Stage 5F – Stage 5L — Finance research workers (filings, sentiment, technical, fundamental, company strategy, pattern detection, Radar candidates).
- Stage 5M — Real-send activation for email alerts (after Resend domain verification). Separate non-design stage.

## Later (Stage 6 advanced visible surfaces — depend on Stage 5)

- Stage 6A — Source Room live.
- Stage 6B / 6C — Intel detail drawer technical / fundamental context live.
- Stage 6D — Intel detail drawer Business story / Company strategy primer / artifact-backed "What would make this thesis wrong" live.
- Stage 6E — Today "What I learned today" capsule live.
- Stage 6F — Journal Lessons surface + archive live.
- Stage 6G — Radar live.
- Stage 6H — Command bar live AI.

## Later (deferred / design-dependent)

- Real tax-lot / wash-sale guardrail logic on top of the per-item finalization + plan-rollup contract. Design-dependent: requires explicit tax-lot / trade-history source decisions before any build can start; do not auto-promote into Now.

## Completed

- **Stage 5D — Evidence Completeness Scoring v1** (PR open 2026-05-18). Pure deterministic backend module. Evaluates 8 requirements (present/missing/not_applicable) and assigns COMPLETE/PARTIAL/THIN/NOT_EVALUABLE band. No numeric 0–100 scores. Consumes Stage 5B credibility and Stage 5C contradiction assessments. `evidence_completeness_assessment` injected into every new artifact payload as Step 6 in `write_artifact()`. Hard rules: editorial-only/UNKNOWN-only → THIN, contradictions → PARTIAL, non-comparable → THIN. 49 tests. No SQL.

- **Stage 5C — Contradiction Detector v1** (merged PR #370, 2026-05-18). Pure deterministic backend module. Groups comparable facts by (claim_key/metric_name, fact_kind, period, as_of). Detects numeric (1% tolerance), boolean, and text-exact conflicts. No-fact/non-comparable → not_evaluable (honest). `contradiction_assessment` injected into every new artifact payload. 41 tests. No SQL.

- **Stage 5B — Source Credibility Registry v1** (merged PR #369, 2026-05-18). Deterministic backend registry classifying all 10 source_kinds into 5 authority bands. Injected into `write_artifact()` — every new artifact payload includes `source_credibility_assessment`. 83 tests. No SQL.

- **Stage 5A — Research Artifact Store substrate + writer scaffolding** (merged PR #367 on 2026-05-18). SQL migrations 017 + 023 applied to Supabase. `research_artifact_service_v1.py` — narrow typed API with user-scoped idempotency, scope-aware clean replacement (full evidence lane `(user_id, artifact_type, skill_pack, scope_kind, COALESCE(ticker, ''))`), portfolio-scope IS NULL filter, `query_active_artifacts()` helper. `WorkerOutput.ticker` → `Optional[str]`. 60 tests.

- **Stage 4C — Intel Investment Committee Redesign** (merged PR #361). `IntelV3Primitives.tsx` + redesigned Cockpit/Card/Drawer. `action-*` token routing; accessible drawer; 56 contract tests. No backend, no SQL.

- **Stage 4B — Today Command Center** (merged PR #359). The Brief + Act Today + Risk Pulse + Deploy Ready + Watchtower Summary from existing Intel v3 / Deploy v3 / Watchtower data. "What I Learned Today" Coming-Later chrome (Stage 6E activates). 49 unit tests. Hydration-safe date. No backend, no SQL.

- **Stage 4A — Design System Foundation + App Shell Reset** (merged PR #358). Obsidian dark + Paper light token palettes on main; Atelier Green `#2EC27E`; DM Serif Display + Inter + JetBrains Mono via `next/font/google`; named CSS vars for all glass/selection/scrollbar surfaces; engraved SideNav + glass BottomNav; Coming-Later utility; reduced-motion support. 4 frontend files. No backend, no SQL.

- **Stage 3G — Alert Center UI v1** (merged). Read-only Alert Center at `/dashboard/alerts` reading `GET /api/v1/alert-candidates` + `GET /api/v1/alert-delivery-outbox`. Nav added to BottomNav and SideNav. Dry-run safety banner always visible. Plain-English status labels, severity pills, empty/loading/error states. 28 unit tests on pure mapping helpers in `src/lib/alert-center.ts`. No SQL, no email-delivery changes.

- **Stage 3F — Email Delivery Production Activation Config** (merged PR #355). Added `email_delivery` PROCESS_TYPE to `railway.toml` and `Procfile`. Entrypoint docstring expanded with staged dry-run → real-send activation instructions. Dry-run pass confirmed on Railway. Resend domain verification still pending; real-send remains parked.

- **Stage 3E — Resend Email Delivery Worker v1** (merged PR #354, SQL 022 applied). Claim-before-send safety. 38 tests. `alert_email_delivery_summary` structured log. Worker separate from Watchtower.

- **Stage 3D — Alert Delivery Outbox v1** (merged PR #353, SQL 021 applied). Provider-neutral `alert_delivery_outbox` table. Pure delivery policy, outbox service with idempotent persistence + 24h noisy-repeat suppression + exact-dedupe-before-suppression ordering, fail-soft Step 5 in hook for all returned candidate rows. `GET /api/v1/alert-delivery-outbox`. No external delivery, no provider SDKs. 109 tests pass.

- **Stage 3C — Watchtower alert candidate generation hook** (merged PR #352). `watchtower_alert_candidate_hook_v1.py` wired after certified Intel v3 snapshot publishes via `compare_and_republish()` / `republish_after_analyst_eligibility()`. Candidates auto-generated on each Watchtower cycle. Fail-soft; errors logged but never break Intel/Watchtower publication. 23 tests pass. SQL 020 already applied.

- **Stage 3B — Alert Trigger Policy v1** (merged PR #350, SQL 020 applied). Pure deterministic policy module `alert_trigger_policy_v1.py` + `AlertCandidateService` + `watchtower_alert_candidates` table (migration 020) + `GET /api/v1/alert-candidates`. Evidence bands: STRONG/PARTIAL actionable; THIN/SUPPRESSED/blank suppress. Feedback suppression: executed indefinite; ignored/not_relevant/too_risky 7d; snoozed 14d default or `cooldown_until`. `action_feedback_events.cooldown_until` added via ALTER TABLE. 79 tests pass.

- **Stage 3A — Action Feedback Foundation v1** (merged PR #349). Append-only `action_feedback_events` table + service + router. SQL migration 019. 22 tests pass. Feedback stored as evidence/context only — no Intel v3 decision mutation, no Deploy sizing change, no Watchtower behavior change.

- **PR 3B Activation Guard: evidence mapping version guard** (Stage 3, merged PR #348). Ensures production does not keep serving pre-PR #347 snapshots after PR #347 merges. `EVIDENCE_MAPPING_VERSION="analyst_verdict_synthesis_v1"` in `evidence_mapping_version_v1.py`; all republish paths treat mapping-version mismatch as a recertification trigger (zero-LLM). 17 backend + 4 frontend tests. Evidence-mapping loop complete.

- **Build 3 PR 3B: Analyst_verdict trusted-signal mapping** (Stage 3, merged PR #347). Root cause: `data_quality_label="MEDIUM"` hardcoded fallback and absent `intel_read` synthesis inflated ALL cards to PARTIAL regardless of analyst content. Fix: synthesize `intel_read.trusted_signals` from `primary_driver` / `action_reason` / `key_drivers` in `ReadOnlyEvidenceAdapter.load_cards()`. Fallback phrases excluded. Research artifacts remain locked (`safe_for_decision=FALSE`, counters always 0). 31 synthesis/policy tests + 9 direct adapter-path tests. No SQL, no UI, no policy change. Post-merge log key: `intel_v3_evidence_depth_summary mapped_existing_analyst_signal_count=N` where N > 0.

- **Stage 2 exit validation** (Stage 2, production-passed). $900 and $1,500 Deploy flows validated: BUY sizing totaled planning cash when guardrails allowed; Step 3 actual logging/manual rows/history/accounting worked; Evaluate captured recent baseline and rendered older comparison. All five exit gates confirmed.

- **Build 3 PR 3A: Source-pack status + evidence-depth observability** (Stage 3, merged PR #344 + cleanup hotfix PR #345). PR #344: `_build_source_pack_status()` derives real status from `decision.evidence_quality`. PR #345: (1) `intel_v3_evidence_depth_summary` wired to prewarm path via `_log_evidence_depth_summary()` shared helper; (2) `_normalize_legacy_committee_status()` in `get_latest_snapshot()` converts persisted `deferred` → real status at API response time and updates aggregate counts (no DB mutation). 31 + 22 = 53 backend tests total. No SQL, no providers, no Deploy/Watchtower change.

- **Build 3 PR 2B: Visible price/valuation context** (Stage 3, merged PR #341 + PR #342, production-activated and visually validated). PR #341 built the bridge and UI; PR #342 confirmed root cause (flag not set in Railway) and added observability logs. 15 new observability tests. Production rendering is live: `INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED=true` is set on both the main app and Watchtower Railway services. Operational log key: `valuation_context_pr2b_aggregate_summary renderable_context_count=N`.

- **Build 3 PR 2A: Watchtower production loop** (Stage 3, merged). Watchtower process wired as a Railway process type (`PROCESS_TYPE=watchtower`). Kill switch `INTEL_V3_WATCHTOWER_ENABLED` (default on). Loop interval configurable via env (`INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS`, default 60s). Production callables (price refresh, analyst enqueue, Intel republish) from `watchtower_callables_v1`. Analyst LLM not run inline — stale analyst evidence enqueues jobs to analyst worker only. 42 new tests. No SQL, no UI.

- **Build 3 PR 1: Trust-the-band evidence quality visibility** (Stage 3, merged PR #337). Evidence band in visible Intel cards now reflects real evidence quality (AxisBand from the decision kernel), not the conviction label. BUY conviction guardrail promoted from shadow-only to visible policy (Cap 5 in `_compute_conviction`: OK evidence caps HIGH BUY to MEDIUM). STUB removed from `_SPECULATIVE_TICKERS` in both `existing_signal_adapter.py` and `portfolio_governor_lite.py`. 31 new tests. No SQL, no UI changes, no new providers.

- **Build 2.6: Tighten Intel research freshness SLA** (Stage 3, merged). Recommendation SLA 24h → 8h; agent insight SLA 48h → 24h. Worker certification now blocks at the new thresholds. Fast freshness gate queues analyst jobs under new policy. Price/Watchtower/Deploy unchanged. 4 new boundary tests. No SQL, no UI.

- **Build 2.5: Simplified user-facing Intel status** (Stage 3, merged). Replaced large internal certification/debug banner with a compact "Portfolio Intelligence" status area showing a plain-English pill (Ready / Updating / Needs Research / Blocked) and one short line. Backend complexity (worker_certified, agent run IDs, LLM details, evidence class names) hidden behind a collapsible "Diagnostics" drawer. Backend certification contract unchanged; all 415 frontend tests pass. No SQL, no new providers.

- **Build 2: Evidence-grade certification + publish contract** (Stage 3, merged). New `watchtower_intel_republisher_v1.py`: after Watchtower writes fresh prices to `portfolio_snapshots`, `compare_and_republish()` triggers `IntelV3Service.run_prewarm_snapshot()` (zero LLM calls) to re-certify and publish a new `worker_certified` snapshot from the fresh evidence. `GET /intel/v3/snapshot` now includes `evidence_freshness_state` (`certified_current` | `republish_pending`) in every response. Watchtower worker boundary preserved — no `decide()` import in the worker. `build_default_intel_republish_callable()` added to `watchtower_callables_v1.py`. 28 new tests. No SQL.

- **Stage 3.0c** — Intel v3 Full-Portfolio Analyst Evidence Refresh — Replaces the Stage 3.0b.6 6-ticker default with `full_portfolio_analyst_refresh_adapter_v1.py`, which wraps `AgentOrchestrator` UNSCOPED so every stale active position refreshes through the existing full-portfolio LLM pass. After the orchestrator reports at least one successful analyst ticker, `IntelV3Service.run_v3()` re-reads cards via `ReadOnlyEvidenceAdapter.load_cards()` and rebuilds decisions from the refreshed rows. Per-ticker success is sourced from real DB state (matching `agent_insights.run_id` / `recommendations.agent_run_id` against the new agent run); failed / fallback / unpersisted tickers stay stale with no fabricated freshness. Legacy 6-ticker `AnalystRefreshAdapter` retained as `INTEL_V3_ANALYST_REFRESH_MODE=budgeted_subset` emergency mode. 21 new backend tests; all 2520 Intel v3 / orchestrator / freshness / refresh tests green. No SQL, no new providers, no Deploy / Watchtower change, no decide() authority change. Production-validation pending — see Now item.
- **Stage 3.0b.6** — Intel v3 Analyst LLM Refresh Adapter v1 — Narrow budgeted bridge from `EvidenceRefreshOrchestrator` to the existing `AgentOrchestrator` so stale analyst evidence (`recommendations` / `agent_insights`) can be refreshed before deterministic decide(). Per-ticker accounting from real DB row state; deterministic priority sorting (BUY/TRIM → weight → age → A→Z); hard budgets (6 tickers, 6 LLM calls, 90s). `AgentOrchestrator` scope filter (`analyst_refresh_tickers`) confines per-ticker LLM + persistence + recommendation-expire to the selected subset. Banner truth fix: `banner_age_summary` reports recommendation + analyst ages separately. 27 new backend tests; all 177 Intel v3 / orchestrator / freshness tests green. No SQL, no new providers, no Deploy/Watchtower change. Production-validated state pending — see Now item.
- **Stage 3.0a** — Intel v3 snapshot freshness + decision-diff diagnostics. `snapshot_freshness_diagnostics.py` (pure); `ReadOnlyEvidenceAdapter` returns timestamps; `build_snapshot()` embeds diagnostics; `intel_v3_service.run_v3()` fetches previous snapshot, computes diagnostics, emits `intel_v3_freshness_summary` log. Frontend: `IntelV3SnapshotDiagnostics` type + plain-English freshness line in `SnapshotBanner`. 21 new backend tests (all pass). No policy change, no LLM, no provider, no SQL migration, no Deploy/Watchtower scope. Partially certified — pending production validation.
- **Stage 2.9** — Deploy v3 new-cash sleeve ranking + selection_reason — Replaced accidental input-order top-N selection in `apply_new_cash_sleeve_sizing` with deterministic ranking by Intel conviction (HIGH > MEDIUM > LOW), then evidence band (STRONG > OK/PARTIAL > THIN), with ticker A→Z as a stable tie-breaker. Added optional `intel_conviction` / `intel_evidence_band` / `selection_reason` fields on `DeployPlanItem`; conviction/band copied from the Intel adapter; selected BUYs get a plain-English `selection_reason` derived from existing Intel labels (no invented confidence). Mapper surfaces `selection_reason` for BUY rows; UI "why" line in Step 2 reads it automatically. Re-asserted in tests: weak/missing/stale/blocked BUYs are still suppressed upstream and never reach the ranker. 4 new backend ranking tests (27 sleeve tests / 554 deploy tests green) + 2 frontend mapper tests. No Intel v3 authority change, no LLM, no SQL, no providers, no Watchtower.
- **Stage 2.8** — Deploy v3 journal accounting + Evaluate restore + rounding residual — Decision-log history accounting is now action-aware via new pure helpers `classifyActualAction` and `computeJournalTotals` in `deploy-v3-decision-log.ts`. BUY spend (BOUGHT/PARTIAL/REPLACED), manual BUY subset (`is_manual` rows), Trim/Sell (TRIMMED/SOLD), and skipped (SKIPPED/WATCHED/HELD) are tracked separately. `DecisionHistoryEntry` in `deposits/page.tsx` no longer renders a single Invested/Reserved aggregate; it shows BUY spend, optional incl. manual, Trim/Sell when present, and either "Over planned by $X" or "Unallocated $X" — never negative. Manual rows carry a "Manual" badge in the ticker list. Evaluate is preserved on every history row: Deploy v3 snapshots now mirror BUY rows into `recommendation_snapshot.normalized_tickers` so the existing evaluation backend/router/hook handle Deploy v3 logs uniformly; TRIM/SELL excluded from performance math; manual rows do not crash evaluation; insufficient 7d/30d/90d data renders gracefully. Backend `apply_new_cash_sleeve_sizing` distributes leftover whole dollars from floor rounding to selected BUY rows deterministically (top-ranked first; skips suppressed/below-min slots; never exceeds cash_to_deploy) — $1,500 selected BUY rows now usually total exactly $1,500 instead of $1,498. 28 new tests (5 backend rounding-residual + 8 frontend journal accounting + helpers/wiring). No SQL, no providers, no LLM, no Intel/Deploy decision authority change.
- **Stage 2.7** — Editable Deploy v3 execution log + decision-log history restore — Step 3 now allows editing actual dollar amounts per visible recommendation (default = recommended); per-row status enum BOUGHT / PARTIAL / SKIPPED / WATCHED / TRIMMED / SOLD / HELD; user-added manual rows (ticker / BUY|TRIM|SELL / amount / optional note) flagged `is_manual: true` and visually badged; saved log preserves both `recommended_amount` and `actual_amount`. Decision log history (latest 10 deduped logs) restored below Step 3 in the primary Deploy UX, reusing the existing `DecisionHistoryEntry` component (single definition; legacy `DecisionLogMemoryPanel` intentionally retained for legacy fallback path only). Active-plan fingerprint dedupe via `buildDeployV3SessionKey` triggers `updateLog` for the matching log rather than a duplicate create; v3 snapshot mirrors `session_key` and entered/deploy/reserve amounts into `decision_context` for unified history rendering. Plain-English clarity note added: "These are Intel v3 planning recommendations, not broker-executed trades." 8 new pure-helper / page-wiring tests (346 frontend tests pass). api.ts adds one additive optional field (`ActualDecisionItem.is_manual`); no SQL, no providers, no LLM, no Intel/Deploy sizing changes.
- **Stage 2.6D** — Deploy v3 new-cash sleeve sizing v1 — replaces BUY-side current-gap math when `cash_to_deploy > 0` with `deploy_new_cash_sleeve_v1.apply_new_cash_sleeve_sizing`: eligible universe = Intel v3 BUY ACTIONABLE_CANDIDATE only; deterministic input-order selection capped at 5; cash_to_deploy distributed across selected items using saved `target_weight` as a relative guardrail (equal-weight fallback when targets missing or sum to zero); floor rounding caps total ≤ `cash_to_deploy`; minimum trade threshold drops below-threshold selections. HOLD/TRIM/SELL never receive new-cash BUY dollars. TRIM/SELL still use current-gap. Adds `DeployPlan.new_cash_residual_usd` / `new_cash_residual_reason` and surfaces `source.residual_cash` + `source.residual_reason` in the router response (plain-English explanation of any idle planning capital). 18 new backend tests including a production-like 10-BUY $900 case asserting 3–5 BUYs and ≥$450 deployed. api.ts: two additive optional fields on `DeployV3PlanResponse.source`. No SQL, no providers, no LLM, no broker.
- **Stage 2.6C** — Amount-aware Deploy v3 new-cash planning v1 — `cash_to_deploy` query param on `GET /api/v1/deploy/v3/plan`; BUY delta uses `target_weight * (portfolio + cash) - current_value`; total BUY capped at `cash_to_deploy`; cash guardrail replaced with planning capital in new-cash mode; TRIM/SELL unchanged; `amount_aware`/`cash_to_deploy`/`sizing_mode` in source metadata; frontend passes Step 1 amount into hook; Step 2 and Step 3 copy branch on `amount_aware`; 25 new backend + 20 new frontend tests; no SQL.
- **Stage 2.6B** — Deploy v3 decision logging v1 — `DeployV3DecisionLogSection` replaces Step 3 placeholder in Deploy v3 path; `deploy-v3-decision-log.ts` pure helpers; snapshot records `source: "deploy_v3"`, Step 1 amount as context only, visible Step 2 items exactly; no_moves/setup_incomplete → no fake log; 35 contract tests in deploy-v3-decision-log.test.ts; 301 total; build green. No backend changes, no SQL.
- **Stage 2.6A** — Deploy v3 powers Step 1/2/3 flow (patched ×2) — Step 1/2/3 primary UX; Deploy v3 Step 2 data source; `deploy-v3-step2-mapper.ts`; Step 2/3 coherence enforced; 46 mapper contract tests; 266 total; build green. No backend changes, no SQL.
- **Stage 2.5F** — Deploy v3 target allocation setup flow v1 — `DeployV3TargetSetupPanel`; editable target % rows, 98–102% total gate, "use current weights as draft", save calls `PUT /api/v1/portfolio/targets`, invalidates deploy_v3 readiness + plan; `PolicyGuidance` for missing Railway env vars. 29 new contract tests; 200 total; 0 failed. No backend changes, no SQL.
- **Stage 2.5E** — Deploy v3 readiness UI surface v1 — `DeployV3ReadinessPanel` on Deploy page; calls `GET /api/v1/deploy/v3/readiness`; renders gate summary, snapshot status, market value coverage, target allocation gaps, policy status (no values); `useDeployV3Readiness()` hook; `DEPLOY_V3_READINESS_ENDPOINT` constant; `DeployV3ReadinessDiagnostic` TypeScript type; `policyStatusLabel()` helper. 43 new frontend contract tests; 0 backend changes, no SQL.
- **Stage 2.5D** — Deploy v3 production readiness diagnostic v1 — `GET /api/v1/deploy/v3/readiness`; reports all gate statuses, snapshot age, per-ticker market-value coverage, target allocation gaps + total %, policy section (`minimum_trade_configured`, `rounding_policy_configured`, `policy_valid`, `policy_status`; no values exposed), suppression reasons, plain-English `next_required_action`. 49 new tests (42 diagnostic + 7 router readiness); 651 deploy tests total; 0 failed. Backend-only, no SQL, no UI.
- **Stage 2.5C** — Deploy v3 target-allocation + policy readiness hardening v1 — portfolio-level target total bounds (98%–102%) enforced (no cash/residual contract yet; near-full specification required); duplicate DB rows → CONFLICTING trust; invalid policy → UNSUPPORTED fail-safe; 3 new suppression reasons in source metadata. 46 new tests + 38 updated; 485 deploy tests total; 0 failed. Backend-only, no SQL, no UI.
- **Stage 2.5B** — Deploy v3 snapshot market-value source v1 — `PortfolioService.create_snapshot()` enriches `positions_data` with `market_value_usd` per position when price is valid/fresh. Cost basis never promoted. Fail-safe on missing/stale/invalid prices. 33 new backend tests; 72 total for adapter + enrichment (0 failed). No SQL, no providers, no LLM.
- **Stage 2.5A** — Deploy v3 certified sizing source adapter v1 — `deploy_sizing_source_adapter_v1.py` wired into `GET /api/v1/deploy/v3/plan`. Reads `portfolio_snapshots` + `target_allocations` + Settings. Source metadata expanded with readiness gates. Adapter is wired but `exact_dollar_ready` depends on source completeness (see Now). 51 new tests; 4469 total; 0 failed.
- **Stage 2.4B** — Plain-English read-only Deploy v3 UI surface — `DeployV3Panel` on Deploy page; calls `GET /api/v1/deploy/v3/plan`; renders `plan_readiness_status`, counts, Intel v3 authority note, honest sizing-not-connected disclaimer. 25 frontend contract tests; 0 backend changes.
- **Stage 2.4A** — Deploy v3 read-only API endpoint — `GET /api/v1/deploy/v3/plan` live; authenticated, read-only; returns plan rollup from latest Intel v3 snapshot.

## Blocked

- _none recorded_

## Validation Needed

- _none recorded_

## Design Pause Candidates

- Premium cockpit design polish after Deploy / Watchtower loop is stable.
- Real tax-lot / wash-sale guardrail logic — pending an explicit tax-lot / trade-history source design (cost-basis source, lot accounting model, wash-sale window scope).

## Do Not Build Yet

See `docs/product/DO_NOT_BUILD_YET.md`. Highlights:

- auto-trading
- LLM-owned visible financial decisions
- broker execution
- raw metric-heavy UI
- full design sprint before Deploy / Watchtower loop is stable
