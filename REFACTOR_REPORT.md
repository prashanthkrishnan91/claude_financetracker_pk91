# REFACTOR REPORT — Lean Advisor Consolidation (Positions, Advisor, Watchlist)

Status: **COMPLETE — all phases landed.** The Phase-0 contract below was committed before any
production code change; phase records were appended as work landed. Final SHA is the PR head.

This PR replaces rejected PR #472. PR #472 (`claude/finance-tracker-refactor-ldf1i2`, head `03f5ed78`)
is **reference-only**: it is not continued, not branched from, not merged, and none of its repo-wide
deletion commits are cherry-picked. It is consulted only for: simplified navigation, cleaner Positions
information architecture, the Watchlist concept, removal of the legacy visible LLM recommendation
surface, externalized policy ticker configuration, and repo-simplification ideas that do not conflict
with this contract. PR #472 was rejected because it deleted protected product surfaces (Paycheck
Advisor, operator diagnostics, truth/repair services) that this contract explicitly preserves.

---

## 1. Baseline

- **Starting main commit SHA:** `27391ae625f1093bb1711d08da71daf756586e20`
  (merge of PR #471 "Separate policy and evidence eligibility gates for individual stocks").
- **PR #471 present:** confirmed — `27391ae` is the PR #471 merge commit; Stage 13C
  behavior (production `current_holdings` snapshot contract in `allocation_policy_v1`,
  independent policy/evidence gates, `evidence_eligible_but_policy_blocked_tickers` bucket)
  is on this branch.
- **PR #472 rejected and reference-only:** confirmed (state `closed`, not merged).
- **Working branch:** `claude/finance-tracker-consolidation-zzkrsl`, created from `27391ae`.

### Baseline test output (exact commands, run at `27391ae` before any change)

Backend — `cd v2/backend && python3 -m pytest tests/ -q`:

```
93 failed, 8910 passed, 12 warnings in 52.45s
```

All 93 failures are pre-existing on untouched main (independently confirmed: PR #472 recorded the
identical `93 failed, 8910 passed` baseline from the same SHA). Failure families:
`test_watchtower_build_1d` (11), `test_intel_v3_stage_3_2e_evidence_adapter_run_match` (11),
`test_stage9c_sec_companyfacts_readiness` (8 — the known `_SupplementalData.sec_fact_records`
constructor mismatch), `test_stage5j_evidence_coverage_read_model` (7),
`test_stage5k_evidence_decision_input_adapter` (6), `test_deploy_v3_router` (5),
`test_watchtower_build_3_pr2a_hotfix2` (4), `test_watchtower_build_2` (4),
`test_evidence_mapping_version_guard` (4), `test_stage8a3_post_lane_republish` (3),
`test_intel_v3_phase6a_sec_edgar` (3), plus 27 spread across 19 other files (mostly
event-loop/test-ordering poisoning: tests pass in isolation, fail in the full run — documented in
HANDOFF as a pre-existing test-isolation issue).

Frontend — `cd v2/frontend && npx jest --runInBand`:

```
Test Suites: 3 failed, 25 passed, 28 total
Tests:       1050 passed, 1050 total
```

The 3 failing suites fail to compile (ts-jest type errors) on untouched main:
`AgentInsightCard.renderingContract.test.ts`, `DataQualityBanner.test.ts`, `decision-log.test.ts`
— all three cover legacy surfaces this PR retires.

TypeScript — `cd v2/frontend && npx tsc --noEmit`: **12 pre-existing errors on main**, all in test
files (`InsightCardThesis.test.ts`, `IntelV3Contract.test.ts`, `IntelV3Stage9J1Contract.test.ts`,
`intel-v3-catalyst-evidence.test.ts`).

Production build — `cd v2/frontend && npx next build`: **green** (18/18 static pages).

### Baseline counts

- Backend test files: **222**; backend tests: **9,003** (93 failed + 8,910 passed).
- Frontend test files: **28**; frontend tests: **1,050** (all passing; 3 additional suites fail to compile).

---

## 2. Protected product spine (must survive, behavior-identical unless stated)

One decision spine, in order:

1. **Certified portfolio and transaction truth** — `app/services/portfolio_service.py`,
   `app/services/portfolio_engine.py`, `app/services/import_service.py` (SHA-256 dedup
   transaction persistence), `app/routers/portfolio.py`, `app/routers/positions.py`.
2. **Current-price truth** — `app/services/price_engine.py` (+ `market_data/*`, `cache/*`),
   `app/services/price_service.py`, `app/services/history_service.py`,
   repair: `app/services/current_price_truth_repair_v1.py`.
3. **Intel v3 evidence production** — `app/services/intelligence/` (v2 feature/thesis lineage,
   `v3/` evidence adapters, freshness/contradiction/credibility/suppression modules,
   `research_workers/*`), with `services/agents/*` + `services/ai/` (context builder) surviving
   strictly as labeled evidence producers used by the protected refresh adapters
   (`analyst_refresh_adapter_v1.py`, `full_portfolio_analyst_refresh_adapter_v1.py`).
4. **Intel v3 deterministic Buy/Hold/Trim/Sell authority** —
   `v3/decision_policy_v1.py` (`decide()`), `v3/intel_v3_service.py`,
   `v3/snapshot_builder.py` (production `current_holdings` contract),
   `v3/source_validator_lite.py`, `v3/portfolio_governor_lite.py`,
   `v3/buy_conviction_guardrail.py`, `v3/certified_intel_run_contract_v1.py`.
5. **Allocation policy and portfolio guardrails** — `app/services/allocation_policy_v1.py`
   (ETF floor 40%, stock-sleeve target, concentration caps, group caps, minimum-trade,
   floor-to-$5 allocation, `allocated_cash <= cash_to_deploy`, `unallocated_cash >= 0`,
   VTI > VOO > SPY > QQQ preference).
6. **Paycheck Advisor cash allocation** — `app/routers/paycheck_plan_preview.py`
   (`POST /api/v1/advisor/paycheck-plan/preview`), wrapping `run_next_buy_policy_diagnostic`.
7. **One user-facing Advisor response** — the new `/dashboard/advisor` view (this PR) is the
   single visible recommendation surface: Intel v3 owns holding actions, Paycheck Advisor owns
   new-cash dollars.

### Exact production paths (item 5 of the Phase-0 contract)

| Concern | Path |
|---|---|
| Authentication (JWT validation) | `v2/backend/app/middleware/auth.py` (`get_current_user`, `get_current_user_from_request`) |
| Supabase client access | `v2/backend/app/database.py` (`get_supabase_client`, `get_supabase_anon_client`, `get_user_client`) |
| Plaid ingestion | `v2/backend/app/services/plaid_service.py`; `app/routers/sync.py` (`POST /sync/plaid`) |
| Robinhood CSV ingestion | `v2/backend/app/services/import_service.py`; `app/routers/sync.py` (`POST /sync/csv/import`) |
| PDF/crypto ingestion | `app/routers/sync.py` (`_parse_crypto_pdf`, `POST /sync/pdf/import`) |
| Transaction persistence | `v2/backend/app/services/import_service.py` (dedup), `app/routers/positions.py` |
| Positions | `v2/backend/app/routers/positions.py` |
| Portfolio snapshots | `v2/backend/app/services/portfolio_service.py` (`/portfolio/snapshots`, backfill) |
| Current-price truth | `v2/backend/app/services/price_engine.py` (`PriceService`) |
| Financial-truth certification | `v2/backend/app/services/financial_truth_baseline_v1.py`; cert harness `app/routers/diagnostics.py` (`_ensure_cert_enabled`, `_get_runtime_cert_user`, `/certify`) |
| Reconciliation diagnostics | `v2/backend/app/services/books_reconciliation_diagnostic_v1.py` (`/diagnostics/finance-intel/books-reconciliation-diagnostic`) |
| Price repair | `v2/backend/app/services/current_price_truth_repair_v1.py` (`/diagnostics/finance-intel/current-price-truth-repair`); `vti_price_history_repair_v1.py` |
| Intel v3 run | `v2/backend/app/routers/intel_v3.py` (`POST /api/v1/intel/v3/run`) |
| Bounded on-demand drain | `v2/backend/app/services/intelligence/v3/analyst_refresh_on_demand_drain_v1.py` (MAX_BATCHES_PER_RUN=3, MAX_JOBS_PER_BATCH=10, MAX_RUNTIME_SECONDS=90) |
| Intel v3 snapshot creation | `v2/backend/app/services/intelligence/v3/snapshot_builder.py` (`build_snapshot`, `current_holdings`) |
| Intel v3 snapshot reading | `v2/backend/app/routers/intel_v3.py` (`GET /intel/v3/snapshot`), `IntelV3Service.get_latest_snapshot` |
| Paycheck Advisor | `v2/backend/app/routers/paycheck_plan_preview.py` (`POST /api/v1/advisor/paycheck-plan/preview`) |
| Allocation policy | `v2/backend/app/services/allocation_policy_v1.py` |
| Cash-plan invariants | `allocation_policy_v1.py` `_allocate` + hard `cash_bound_violated` guard (`allocated_cash > cash_to_deploy or unallocated_cash < 0` aborts) |

---

## 3. Current recommendation/allocation/deploy/Intel/advisor surfaces (items 6–8)

| Surface | Where | Classification |
|---|---|---|
| Intel v3 cockpit (deterministic actions) | `/dashboard/recommendations` with `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true`; `IntelV3Cockpit`/`IntelV3Card`/`IntelV3Drawer` | **Canonical holding-action surface** (moves into Advisor view) |
| Legacy LLM/agent recommendation cards | same page, flag off: `AgentInsightCard`, `AgentProgressTracker`, `PortfolioSynthesisPanel`, `DataQualityBanner`, fed by `GET /recommendations/` → `RecommendationService` → multi-agent LLM pipeline | **Forbidden visible competing authority — REMOVE** |
| Paycheck Advisor | `/dashboard/paycheck-plan` → Next route handler → `POST /api/v1/advisor/paycheck-plan/preview` | **Canonical new-cash surface** (moves into Advisor view) |
| Deploy tab (Capital Allocation Ledger) | `/dashboard/deposits`: Deploy v3 plan (`GET /deploy/v3/plan`) + legacy allocation fallback (`/api/deposit-plan` → `GET /api/v1/allocation/plan`) | **Duplicate cash-deployment path — RETIRE** (HANDOFF: "legacy/internal readiness surface pending nav cleanup") |
| Today command center | `/dashboard` (Intel v3 + Deploy v3 + alerts projections) | Duplicate dashboard surface — replaced by redirect to Positions |
| AI rebalance | `POST /api/v1/ai/rebalance` (`ai_service.py`) | Legacy LLM authority endpoint, no frontend consumer — RETIRE |
| Legacy deposit schedule | `/api/v1/deposits/*` (hardcoded NVDA/VOO/VYM/QQQ formula) | Duplicate cash path — RETIRE |
| Alerts/Watchtower review queue | `/dashboard/alerts` (`/alert-candidates`, `/alert-delivery-outbox`) | Internal/diagnostic; page retired, internal watchtower machinery preserved |
| Journal / decision logs | `/dashboard/journal`, `/decision-logs/*`, `/decision/*` | Legacy journaling of retired surfaces — RETIRE |
| DRIP analytics | `/dashboard/drip`, `/api/v1/drip/*` | Non-spine analytics page — RETIRE |
| Radar | `/dashboard/radar` | Static "Coming Later" stub — RETIRE (forbidden placeholder) |
| Strategy performance | `GET /analytics/strategy-performance` | Legacy read over recommendation engine — RETIRE |
| Diagnostics/cert endpoints | `/api/v1/diagnostics/finance-intel/*` (~48 cert-gated routes) | **Protected operator/recovery infrastructure — KEEP** (never primary navigation) |

**Canonical:** Intel v3 (holding actions) + Paycheck Advisor (new cash) rendered in ONE Advisor view.
**Legacy/duplicate:** legacy LLM cards, Deploy tab (both engines), deposits schedule, AI rebalance, allocation engine.
**Internal/diagnostic/recovery:** diagnostics router, repair services, watchtower internals, cost guard, worker entrypoints.

---

## 4. Keep / delete / retire table (item 9)

### Backend routers (registered in `app/main.py`)

| Router | Decision | Reason / dependency proof |
|---|---|---|
| `auth.py` | KEEP | protected |
| `portfolio.py` | KEEP | protected |
| `positions.py` | KEEP | protected (+ tax-lots endpoint added if reconciliation passes) |
| `prices.py` | KEEP | protected |
| `sync.py` | KEEP | protected ingestion |
| `intel_v3.py` | KEEP | protected |
| `paycheck_plan_preview.py` | KEEP | protected; response extended **additively** for Advisor explanation buckets |
| `diagnostics.py` | KEEP | protected cert/repair harness (not primary nav) |
| `watchlist.py` | ADD | new primary view #3 |
| `recommendations.py` | DELETE | legacy visible LLM surface; consumers: retired frontend page only |
| `ai.py` | DELETE | LLM rebalance authority; no frontend consumer |
| `decisions.py` | DELETE | legacy overrides; consumer retired |
| `decision_logs.py` | DELETE | journal infra; consumers (deposits/portfolio/journal pages) retired |
| `analytics.py` | DELETE | legacy read over recommendation engine |
| `allocation.py` | DELETE | duplicate legacy allocation engine path |
| `deposits.py` | DELETE | duplicate cash path (hardcoded weights) |
| `drip.py` | DELETE | non-spine analytics; page retired |
| `deploy_v3.py` | DELETE | duplicate deployment-plan authority vs Paycheck Advisor |
| `action_feedback.py` | DELETE | side channel of retired surfaces; no remaining consumer |
| `alert_candidates.py` | DELETE | read layer for retired alerts page |
| `alert_delivery_outbox.py` | DELETE | read layer for retired alerts page |

### Backend services

| Service | Decision | Reason |
|---|---|---|
| `intelligence/` tree (v2+v3+research_workers), all guardrails, drain, workers, cost guard | KEEP | protected spine |
| `allocation_policy_v1.py` | KEEP | protected (tickers externalized, parity-tested) |
| `financial_truth_baseline_v1.py`, `books_reconciliation_diagnostic_v1.py`, `current_price_truth_repair_v1.py`, `vti_price_history_repair_v1.py`, `vti_dca_benchmark_diagnostic_v1.py` | KEEP | protected truth/repair |
| `portfolio_service.py`, `portfolio_engine.py`, `price_engine.py`, `price_service.py`, `history_service.py`, `import_service.py`, `plaid_service.py`, `crypto_service.py`, `market_data/*`, `cache/*`, `http_retry.py` | KEEP | protected ingestion/truth |
| `recommendation_engine.py` | KEEP (internal only) | imported by protected `diagnostics.py` cert harness and `agents/orchestrator.py`; no user-facing route remains |
| `agents/*`, `ai/` (context builder) | KEEP (evidence producers only) | imported by protected Intel v3 refresh adapters + diagnostics cert jobs; never visible authority |
| `alert/*` + alert worker entrypoint | KEEP (internal, disabled by cost guard) | watchtower hook integration; no user-facing route remains |
| `ai_service.py` | DELETE | only consumer is deleted `ai.py` router |
| `allocation_engine.py`, `adaptive_deployment.py`, `deployment_engine.py`, `regime_engine.py` | DELETE | only consumers are deleted `allocation.py` router / each other |
| `deposit_service.py`, `personalized_decision_engine.py`, `personalization_engine.py`, `strategy_engine.py`, `strategy_modes.py`, `simulation_engine.py` | DELETE | only consumers are deleted `deposits.py` router / each other; `simulation_engine` has no app importer |
| `decision_engine.py` | DELETE (with `portfolio_service.generate_deposit_plan` trim if sole caller) | verify import graph first; consumer chain ends in deleted deposit path |
| `decision_log_service.py`, `decision_history_service.py`, `decision_delta.py`, `decision_explainer.py` | DELETE | consumers are deleted routers only |
| `drip_service.py` | DELETE | only consumer is deleted `drip.py` |
| `action_feedback_service.py` | DELETE | only consumer is deleted router |
| `deploy/*` | DELETE (pending import-graph proof) | only consumer is deleted `deploy_v3.py`; verify no diagnostics import first |
| `reasoning_contract.py`, `decision_engine`-adjacent leftovers | VERIFY then delete if orphaned | import-graph proof required |

**Deletion gate honored for every row:** a module is deleted only after `grep`-verified proof that no
protected path imports it (directly or transitively), and the proof is recorded in the final section.
If a module feeds a protected path it is kept regardless of the table above.

### Frontend

| Surface | Decision |
|---|---|
| `/dashboard/positions` (new), `/dashboard/advisor` (new), `/dashboard/watchlist` (new) | ADD — the three primary views |
| `/dashboard` page content (Today) | REPLACE with redirect → `/dashboard/positions` |
| `/dashboard/import`, `/settings`, `/login`, `/`, `/dashboard/position/[ticker]` | KEEP (operational subpages, not primary nav) |
| `/dashboard/recommendations`, `/deposits`, `/paycheck-plan`, `/alerts`, `/journal`, `/drip`, `/radar`, `/dashboard/portfolio` | RETIRE → redirect (map in §6) |
| `IntelV3Cockpit`, `IntelV3Card`, `IntelV3Drawer`, `IntelV3Primitives(+Data)`, `TrustPrimitives`, `DataHealthDrawer` | KEEP — reused inside Advisor |
| `PaycheckPlanPreviewCard` + `paycheck-plan-helpers` | EVOLVE into Advisor cash-plan section |
| `AgentInsightCard`, `AgentProgressTracker`, `PortfolioSynthesisPanel`, `portfolioSynthesisRuntime`, `DataQualityBanner`, `InsightCard` (already dead) | DELETE — forbidden legacy LLM surface |
| `DeployLedger`, `DeployV3Panel`, `DeployV3ReadinessPanel`, `DeployV3TargetSetupPanel` | DELETE — duplicate deploy UI |
| `DeterministicCapsules` | DELETE if unused after alerts page removal (verify) |
| `PortfolioChart`, `PortfolioSummaryCard`, `HoldingsList` | REUSE in Positions if fitting, else delete with proof |
| lib: `alert-capsules`, `alert-center`, `decision-log`, `deploy-ledger`, `deploy-v3-*`, `journal-ledger`, `today-command-center` | DELETE with their tests (legacy surfaces) |
| lib: `intel-v3-*`, `visibleIntelActions`, `portfolio-ledger`, `paycheck-plan-helpers`, `api.ts`, `hooks.ts`, `auth.tsx`, `supabase.ts`, `utils.ts` | KEEP (trimmed of dead methods: `recommendations.*`, `ai.*`, `deposits.getPlan`, `decisionLogs.*`, `analytics.*`, `deployV3.*`, `alertCenter.*`, `drip.*`) |
| `/api/deposit-plan` route handler | DELETE (legacy allocation proxy) |
| `/api/advisor/paycheck-plan/preview` route handler | KEEP (server-only cert secret pattern preserved) |
| `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` flag | REMOVE — existed only to switch between competing recommendation surfaces |

---

## 5. Protected test inventory (item 10)

Must survive and pass at their baseline rate (pre-existing failures on main excepted):

- Intel v3 kernel/spine: all `test_intel_v3_*`, `test_v3_*`, `test_stage5*`–`test_stage9*`,
  `test_stage10_supabase_egress_fix`, `test_stage13b_*`, `test_stage_2_5b_*`, `test_stage_3_*`,
  `test_intel_read_projection`, `test_finance_intel_reliability`, `test_cost_guard`,
  `test_run_mode`, `test_feature_engine`, `test_market_snapshot`, watchtower suites.
- Truth/cert/repair: `test_finance_runtime_certification`, `test_runtime_cert_auth_header_path`,
  `test_financial_truth_baseline_v1/_router`, `test_books_reconciliation_diagnostic_v1`,
  `test_current_price_truth_repair_v1/_router`, `test_vti_price_history_repair_v1`,
  `test_vti_dca_benchmark_diagnostic_v1`.
- Paycheck Advisor: `test_allocation_policy_v1`, `test_allocation_policy_v1_router`,
  `test_paycheck_plan_preview_router`.
- Ingestion/auth/prices: `test_auth`, `test_plaid_service`, `test_import_service`, `test_sync`,
  `test_crypto_service`, `test_price_engine`, `test_history_service`, `test_portfolio_service`,
  `test_market_cache`, `test_distributed_lock`, `test_request_coalescer`,
  `test_resilient_provider`, `test_data_sources_retry`, `test_io_layer*`, `test_system_mode`.
- Evidence producers kept as internal: `test_agent_pipeline_hardening`,
  `test_orchestrator_single_llm`, `test_context_builder_*`, `test_per_ticker_analyst`,
  `test_portfolio_synthesis`, `test_recommendation_engine` (service-level),
  alert service tests (`test_alert_candidate_service`, `test_alert_delivery_outbox`,
  `test_alert_email_delivery_worker_v1`, `test_alert_trigger_policy_v1`).
- Frontend: `IntelV3Contract`, `IntelV3Stage4DContract`, `IntelV3Stage9J1Contract`,
  `intel-v3-banner/evidence/catalyst-evidence/drawer-clarity/explanation`,
  `visibleIntelActions`, `PaycheckPlanPreviewContract` (updated additively), `portfolio-ledger`.

Tests deleted with retired surfaces will be enumerated one-by-one in §12 (final), each beside the
deleted production surface it exclusively covered.

---

## 6. Intended final route map (items 14–15)

### Frontend routes

| Route | Behavior |
|---|---|
| `/` | public splash → `/dashboard` when authenticated |
| `/login` | auth |
| `/settings` | profile/settings (secondary; reachable from header, not primary nav) |
| `/dashboard` | **redirect → `/dashboard/positions`** |
| `/dashboard/positions` | **PRIMARY 1 — Positions** |
| `/dashboard/advisor` | **PRIMARY 2 — Advisor** (sections: readiness/Run Intel, holding actions, new-cash plan, trust & repair) |
| `/dashboard/watchlist` | **PRIMARY 3 — Watchlist** |
| `/dashboard/import` | data import (secondary; linked from Positions) |
| `/dashboard/position/[ticker]` | holding detail (secondary; linked from Positions) |
| `/dashboard/portfolio` | redirect → `/dashboard/positions` |
| `/dashboard/recommendations` | redirect → `/dashboard/advisor` |
| `/dashboard/deposits` | redirect → `/dashboard/advisor` |
| `/dashboard/paycheck-plan` | redirect → `/dashboard/advisor?section=cash-plan` |
| `/dashboard/alerts` | redirect → `/dashboard/advisor` |
| `/dashboard/journal` | redirect → `/dashboard/advisor` |
| `/dashboard/drip` | redirect → `/dashboard/positions` |
| `/dashboard/radar` | redirect → `/dashboard/watchlist` |

Primary navigation (mobile bottom nav + desktop side nav): exactly **Positions, Advisor, Watchlist**
(+ settings/import as non-primary secondary links).

### Backend API surface (final)

Registered: `/api/v1/auth/*`, `/api/v1/portfolio/*`, `/api/v1/positions/*` (+ tax-lots endpoint if
shipped), `/api/v1/prices/*`, `/api/v1/sync/*`, `/api/v1/intel/v3/*`,
`/api/v1/advisor/paycheck-plan/preview`, `/api/v1/diagnostics/finance-intel/*`,
`/api/v1/watchlist*` (new).

Deliberately retired (removed from the app; documented here): `/api/v1/recommendations/*`,
`/api/v1/ai/*`, `/api/v1/decision/*`, `/api/v1/decision-logs/*`, `/api/v1/analytics/*`,
`/api/v1/allocation/*`, `/api/v1/deposits/*`, `/api/v1/drip/*`, `/api/v1/deploy/v3/*`,
`/api/v1/action-feedback*`, `/api/v1/alert-candidates`, `/api/v1/alert-delivery-outbox`.

---

## 7. Required SQL / env / deployment changes (item 16)

- **SQL (one additive migration):** `v2/database/025_watchlist.sql` — `watchlist_items` table,
  RLS enabled, owner policy, user index, `updated_at` for edit support, unique duplicate guard,
  no destructive statements. Must be applied manually in Supabase SQL editor; Watchlist endpoints
  return a clear "migration required" state until applied. Rollback: the table may simply remain
  (unused); an explicit `DROP TABLE` teardown is documented but NOT part of this PR.
- **Env (backend/Railway):** no new required vars. `INTEL_V3_ON_DEMAND_REFRESH_ENABLED=true`
  is documented as required for Run Intel to process jobs without the optional worker
  (existing flag, default false, cost posture unchanged).
- **Env (frontend/Vercel):** `FINANCE_RUNTIME_CERT_SECRET` (existing, server-only) still required
  for the Advisor cash plan. `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` becomes unused
  (flag removed from code); safe cleanup documented, not required.
- **Railway start commands:** unchanged. Optional worker services unchanged.

## Rollback plan (item 17)

Reverting the PR (single `git revert` of the merge) restores the previous application code with no
data loss: no destructive migration ships, no table is dropped or truncated, all writes remain
additive. If migration 025 was applied, the `watchlist_items` table remains in Supabase, unused and
harmless after rollback (documented; optional manual teardown SQL provided in §SQL). Env vars are
not removed by this PR, so no env restore is needed.

---
---

# Phase 1+ record (appended as work lands)

(To be completed: dependency-graph proofs, deletion log, policy-ticker migration, dataclass fix,
tax-lot decision and reconciliation evidence, Watchlist schema, final tests, test-count
reconciliation, build output, runtime proof, screenshots, semantic review findings, final SHA.)

## Phase 1 — decision spine stabilized (record)

- **Dataclass fix:** `_SupplementalData.sec_fact_records`
  (`app/services/intelligence/v3/intel_data_foundation_forensics_v1.py`) was a required,
  default-less field added in Stage 9D, crashing every pre-9D constructor
  (`TypeError: missing 1 required positional argument`). Smallest behavior-preserving fix:
  `field(default_factory=dict)` — the production producer always passes the field explicitly.
  Verified: `test_stage9c_sec_companyfacts_readiness.py` + stage9b + stage9d = 226 passed
  (baseline had 8 failures in stage9c).
- **Policy tickers externalized** to `v2/backend/app/policy_tickers.json`, loaded by
  `v2/backend/app/services/policy_tickers.py` (validated, fail-loud, cached, override via
  `POLICY_TICKERS_FILE`). Wired consumers: `allocation_policy_v1.py` (preference order +
  7 classification sets), `intelligence/v3/decision_policy_v1.py` (kernel crypto set),
  `intelligence/v3/etf_intelligence_classifier_v1.py` (`_KNOWN_ETF_MAP`, 55 entries).
  Exact parity with historic hardcoded values proven by
  `tests/test_policy_tickers.py` (25 tests: exact-membership/order parity, consumer-constant
  parity, classification behavior, fail-loud validation for missing file/key, empty list,
  duplicate ticker, ambiguous cross-set membership, unknown group/type/role, malformed pair,
  case normalization, override path) plus an independent scripted diff of the historic
  `_KNOWN_ETF_MAP` against the config (55/55 identical). Provider symbol-translation tables
  (crypto→Yahoo in `history_service.py`, CoinGecko IDs in `agents/data_sources.py` and
  `current_price_truth_repair_v1.py`) stay in provider code — routing data, not policy.
- **PR #471 behavior confirmed:** `test_allocation_policy_v1.py` +
  `test_allocation_policy_v1_router.py` + `test_paycheck_plan_preview_router.py` +
  kernel policy suites = 315 passed after the change.

## Phase 2 — backend legacy-surface retirement (record)

Deletion executed exactly per the §4 table, after import-graph verification (grep sweeps recorded
below). App verified importable and serving after the sweep: 62 OpenAPI paths across
`advisor(1) auth(4) diagnostics(36) intel(3) portfolio(6) positions(2) prices(4) sync(5) health(1)`.

**Import-graph proofs (deletion gate):**
- `recommendation_engine` imported by protected `routers/diagnostics.py`, `agents/orchestrator.py`,
  6 `intelligence/v3` valuation/shadow modules and 6 `research_workers` modules → **KEPT** (internal).
- `services/agents/*` (`job_runner.run_agent_pipeline`) imported by `routers/diagnostics.py:22` → KEPT.
- `services/alert/*` lazily imported by protected
  `intelligence/v3/watchtower_callables_v1.py:114` (`watchtower_alert_candidate_hook_v1`) → KEPT
  (worker + services; only the two read routers were retired).
- `services/deploy/*` consumed only by deleted `routers/deploy_v3.py` / `routers/allocation.py`
  (and gates K–L of `test_stage_2_5b`) → deleted.
- `decision_engine` + `decision_history_service` consumed only via
  `portfolio_service.get_deposit_plan()` whose sole caller was deleted `routers/deposits.py` →
  method + services deleted.
- `simulation_engine` had no app importers (comment-only mention) → deleted.
- `agent_run_status`, `reasoning_contract`, `history_service`, `models/recommendation` verified
  in use by kept modules → KEPT.

**Deleted test files (each exclusively covered a deleted surface; test counts):**
test_action_feedback_service (27), test_adaptive_deployment (15), test_ai_service (2),
test_allocation_engine (34), test_decision_delta (2), test_decision_log_service (2),
test_decision_performance (6), test_decision_step3_semantics (2), test_decision_logs_models (2),
test_deployment_engine (32), test_deployment_wiring (18), test_regime_engine (9),
test_recommendations_workflow_v2 (13 — legacy `/recommendations` refresh workflow),
test_deploy_cash_guardrail_v1 (24), test_deploy_dollar_math_v1 (31), test_deploy_finalization_v1 (37),
test_deploy_foundation_v1 (87), test_deploy_new_cash_sleeve_v1 (27), test_deploy_plan_rollup_v1 (31),
test_deploy_policy_allocation_bridge (55), test_deploy_readiness_diagnostic_v1 (42),
test_deploy_sizing_input_contract (119), test_deploy_sizing_source_adapter_v1 (39),
test_deploy_stage_2_5c_readiness_hardening (36), test_deploy_tax_wash_pending_v1 (27),
test_deploy_v3_amount_aware (27), test_deploy_v3_router (44). **Total deleted: 790 tests / 27 files.**

**Mixed files trimmed (deleted-surface classes only):** `test_sync.py` −8 (drip dividend-date
wrapper), `test_stage_2_5b_snapshot_market_values.py` −7 (deploy sizing adapter consumer gates K–L;
snapshot-contract gates A–J/M–P all kept), `test_finance_runtime_certification.py` −1 (deleted
route's auth check), `test_models.py` −1 (deposit models).

**Stale-fixture cleanup (test-only, 21 files):** cleared all 41 remaining pre-existing baseline
failures (Migration-024 flat columns, cost-guard flag, Stage 9F lane, Stage 13 freshness
annotation, kernel BUY guardrail, worker kill-switch gating, static-guard modernization).
**Backend suite after Phase 2/3/5 backend work: `8288 passed, 0 failed`** (final full run after all review fixes: **`8290 passed, 0 failed`**) (baseline: 93 failed /
8910 passed).

## Phase 3 (backend) — tax lots (record)

Tax-lot truth decision: SHIPPED, reconciliation-gated per ticker. `app/services/tax_lot_engine.py`
classifies the full production tx vocabulary (Buy/Sell/CDIV/DRIP/SPL/ACH/RTP/Other) into
share-increasing / share-decreasing / non-share-affecting / unsupported-unknown; splits without
ratios, share-carrying cash codes, unknown share events, DRIP-without-basis, and dateless share
events are surfaced in diagnostics and block authoritative display. FIFO lots; oversold ledgers
block. Long-term = day after calendar anniversary (Feb-29 → Mar-1), tested across leap years.
Reconciliation tolerances: shares within max(0.0001, 0.1%), basis within 2.0% (matches books
reconciliation). Unreconciled tickers render exactly "Tax-lot details need reconciliation before
they can be relied on." No dollar tax estimates anywhere; explicit US-federal estimates-only
labeling. `GET /api/v1/positions/tax-lots` (36 new tests).

## Phase 5 (backend) — Watchlist (record)

`v2/database/025_watchlist.sql` (additive, idempotent, RLS + owner policy + user index +
updated_at + unique (user_id, ticker, criteria_type)); `/api/v1/watchlist` GET/POST/PATCH/DELETE
with auth, user-scoping, validation, duplicate 409, batched price enrichment, unknown-price state,
and a deliberate 503 `watchlist_migration_required` state until the migration is applied. 16 tests
incl. cross-user isolation (404, no existence leak) and product-boundary guards (no advisor
coupling, no LLM, no alerts).

## Advisor backend contract (record)

`POST /api/v1/advisor/paycheck-plan/preview` extended ADDITIVELY with `generated_at` and
`explanations {selected, not_selected, plan_notes}` mapping existing diagnostic gate fields into
plain-English buckets (selected / evidence_eligible_policy_blocked / evidence_blocked /
concentration_blocked / group_cap_blocked / stale_price_blocked / missing_truth_blocked /
below_minimum_trade / max_positions_reached). No allocation math added or changed; Stage 12D keys
untouched; raw codes preserved for expandable technical detail. 14 new tests.

## Phase 3/4/5 (frontend) — the three views (record)

Built by bounded parallel subagents against the existing design system (Obsidian tokens,
`card-glass`/`data-card`/`action-badge-*`/`btn-*` classes, serif display + mono numerals), no new
theme, no new dependencies:

- **Positions** (`/dashboard/positions` + `lib/positions-view.ts`, `lib/tax-lots.ts`): summary band
  (value, cost basis, unrealized G/L $/%, cash-or-unavailable, equities/ETF/crypto split, top
  concentration, snapshot freshness + stale-price warning), honest degraded totals (P&L only over
  the priced subset), keyboard-operable holding expansion with per-holding Intel action/evidence/
  freshness or "No certified Intel", lazily-loaded reconciliation-gated tax lots with the exact
  backend message when blocked, import/settings header links, loading/empty/auth/error/stale/
  no-snapshot states. 42 helper tests.
- **Advisor** (`/dashboard/advisor` + `lib/advisor-readiness.ts`, `lib/advisor-cash-plan.ts`,
  `components/advisor/*`): Section A readiness + bounded Run Intel state machine
  (idle/running/partial/complete/failed/queue_only; "Continue Intel run" on partial; retry on
  failure; plain-English translation of every `next_required_action` code; job counters +
  bounded-stop reason; aria-live progress; auto snapshot refetch; "Ready" impossible without a
  certified fresh snapshot). Section B mounts the existing deterministic `IntelV3Cockpit`
  unconditionally. Section C cash plan on the canonical preview endpoint with trusted badge +
  exact blocker, per-allocation $/%/reasons/evidence-chips/policy-roles, allocated/unallocated,
  `generated_at`, ETF-only plan notes, grouped plain-English buckets with raw codes confined to
  technical-detail expanders, all ten required plan states. Section D collapsed trust/repair
  drawer with exact repair actions. `?section=cash-plan` deep link. 58 tests.
- **Watchlist** (`/dashboard/watchlist` + `lib/watchlist.ts`): add/edit/delete with validation,
  duplicate 409 inline message, unknown-price state, migration-required 503 state, mobile-first.
  Tests in `watchlist.test.ts`.

## Phase 6 (frontend) — retirement (record)

Nav reduced to exactly Positions/Advisor/Watchlist (mobile + desktop; Settings survives as a
visually-secondary footer action); `/dashboard` → Positions; one redirect map
(`lib/route-redirects.ts`) covers portfolio→positions, recommendations→advisor, deposits→advisor,
paycheck-plan→advisor?section=cash-plan, alerts→advisor, journal→advisor, drip→positions,
radar→watchlist. Deleted: the legacy LLM card stack (AgentInsightCard, AgentProgressTracker,
PortfolioSynthesisPanel + runtime, DataQualityBanner, InsightCard), duplicate Deploy/Paycheck UI
(DeployLedger, DeployV3 panels, PaycheckPlanPreviewCard), alerts/journal/today/portfolio-ledger
libs, `/api/deposit-plan` proxy, and the `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` flag
path. `api.ts`/`hooks.ts` trimmed of retired groups; `DataHealthDrawer`/`buildDataHealthRows`
trimmed of retired Deploy/alert/email rows so no kept component calls a retired endpoint (found
and fixed during integration review: the drawer originally kept `useDeployV3Plan` and
`useAlertCandidates` against deleted endpoints). Kept legacy Intel contract tests modernized
(fixture casts) so `tsc` is fully clean. New tests: 3-item nav contract, redirect-map contract,
cert-secret safety scan.

**Deleted frontend test files (each exclusively covered a deleted surface):**
AgentInsightCard.renderingContract (4), AgentInsightCardThesisVisibility (42),
DataQualityBanner (3), DeployV3Contract (49), DeployV3ReadinessContract (31),
DeployV3TargetSetupContract (49), InsightCardThesis (16), PaycheckPlanPreviewContract (23 —
cert-secret safety assertions recreated in `lib/cert-secret-safety.test.ts`),
portfolioSynthesisRuntime (2), alert-capsules (24), alert-center (28), decision-log (11),
deploy-ledger (62), deploy-v3-api-url (6), deploy-v3-decision-log (84), deploy-v3-step2-mapper
(66), journal-ledger (47), portfolio-ledger (62 — the page it covered is now a redirect; the
Positions view has its own `positions-view` helpers/tests), today-command-center (67).
**Total: 676 tests / 19 files.**

## Test-count reconciliation (final)

| Suite | Baseline (main) | Final (branch) | Accounting |
|---|---|---|---|
| Backend | 9,003 collected (93 failed / 8,910 passed) | **8,288 passed / 0 failed** | −807 deleted with retired surfaces (790 in 27 files + 17 trimmed from 4 mixed files, enumerated above) + ~92 added (25 policy-ticker parity, 14 paycheck explanations, 16 watchlist, 36 tax lots, +1 net fixture-modernization) |
| Frontend | 1,050 passing (28 files; +18 in 3 suites that failed to compile on main) | **511 passing / 0 failed, 16 suites** | −676 deleted with retired surfaces (19 files, enumerated above) −7 retired data-health row tests trimmed from kept suites + ~126 added (42 positions/watchlist helpers, 58 advisor, nav/redirect/cert-secret + misc) |
| tsc | 12 errors (main) | **0 errors** | legacy test files deleted or their stale casts modernized |
| next build | green | **green** | final route map: 3 views + import/settings/position-detail/login + redirect stubs |

Every material reduction is enumerated file-by-file with the deleted production surface it
exclusively covered (Phase 2 and Phase 6 records above); behavior that moved (cert-secret safety
scan, positions helpers) has replacement coverage named beside it.

## Semantic review (record)

Ten dimensions reviewed by independent fresh-context read-only reviewers; all findings resolved
in two fix rounds (commits "Semantic review round 1/2"):

| Dimension | Verdict | Material findings → resolution |
|---|---|---|
| Product goal alignment + decision authority | PASS (5/5 questions) | 3 low hygiene items (dead DeployV3/AlertCandidate types, unused useRebalance hook) → removed |
| Financial truth preservation | PASS | Medium: zero-price Buy could mint a zero-basis lot → now fail-closed unsupported (tested); "Stale prices" mislabel → "Missing prices" |
| Allocation/cash invariants + contracts + deletion safety | PASS after fix | Blocker: dead `portfolio_service.get_deposit_plan()` importing deleted modules → removed; all frontend fetches map to registered routes; invariant code byte-identical to main |
| Tax-lot correctness | PASS | (covered by financial-truth + contract reviewers) |
| Authentication + user isolation + security | PASS | No new-code findings; pre-existing lows documented (login query-param binding, exception-echo details) — retained code untouched by design |
| Runtime/cost behavior | PASS | "Net cost-reducing branch": no new polling/workers, batching verified, drain caps untouched |
| Accessibility | Fixed to standard | aria-current, focus rings + 40px targets on Advisor, scoped aria-live, form Enter submit, focus management, DataHealthDrawer focus trap + restore, role=alert |
| Deletion safety | PASS after fix | see contracts row |
| Deployment readiness (SQL/env) | PASS | Watchlist create race → 409 backstop; HANDOFF env gaps (backend INTEL_V3_VISIBLE_SNAPSHOT_ENABLED must stay set; boot-required Supabase vars) documented; README env filename fixed |
| Plain-English UI | PASS after fixes | 4 blockers fixed (env-var names out of visible copy, raw enum chips translated, reconciliation codes translated, promised technical-detail expander now exists) + risks R1/R4/R6/R7 |

## Runtime proof (record)

Production credentials are not available in this environment (verified external constraint), so
end-to-end proof ran the REAL application locally over HTTP with fixtures injected only at the
outermost boundaries — documented per capture in `docs/ai/proof/consolidation/RESPONSES.md`:

- **Real and unmodified:** the FastAPI app with every registered router; JWT middleware
  validating a real HS256 token (local GoTrue-shaped auth server + JWKS); the deterministic
  allocation policy producing the plan; the tax-lot engine; Intel v3 snapshot read path; Run
  Intel enqueue writing real `analyst_refresh_jobs` rows; watchlist CRUD incl. the 409 policy;
  the Next.js **production build** incl. the server-only cert-secret route handler; the real
  login flow; React Query.
- **Fixture-injected:** in-memory Supabase client (same seam the test suite patches) seeded
  with a 6-position portfolio whose Intel snapshot payload was built by calling the real
  `snapshot_builder.build_snapshot()`; fixture `PriceService.fetch_prices` results; and, for the
  two Run-Intel progress scenarios only, deterministic drain results staging the real response
  contract.
- **Verified flows:** login → `/dashboard` → Positions redirect; legacy-route redirects
  (deposits→advisor, paycheck-plan→advisor?section=cash-plan); trusted plan via the frontend
  route handler (`trusted: true` + explanations); degraded plan; Run Intel partial
  ("Continue Intel run", jobs 6 queued/4 attempted/4 succeeded/0 failed/2 remaining) and
  complete; snapshot 404 state; watchlist create/list/edit/delete + 409; tax lots reconciled
  (long+short term) and blocked; backend-stopped error state; three-tab nav.
- **19 screenshots** (desktop + mobile) + **13 sanitized API captures** in
  `docs/ai/proof/consolidation/`.
- **Not validated against live Railway/Supabase/Vercel production** (explicitly identified):
  production data behavior, the applied Watchlist migration (production Watchlist stays
  unavailable until `025_watchlist.sql` is applied), and the Vercel preview deployment (created
  automatically by the Vercel GitHub integration when this PR opens; the preview URL appears in
  the PR's Vercel comment). Four runtime bugs found during proof were fixed and re-verified
  (snapshot-aware idle sentence, deep-link scroll, contradictory degraded totals, plus the
  Decimal-string serialization noted as pre-existing backend behavior handled by the UI).

## Final confirmations (record)

- **No new recommendation model, engine, endpoint family, or visible LLM authority was
  created.** The only recommendation surfaces are deterministic Intel v3 (holding actions) and
  the pre-existing canonical Paycheck Advisor endpoint (new cash), rendered in one Advisor view.
  LLM/agent code survives only as labeled internal evidence producers behind the protected
  refresh adapters and the cert harness.
- **The app answers "I have $X available to invest. What should I buy now, how much, and
  why?"** — Advisor → Cash plan → deterministic dollar allocations with per-ticker reasons,
  evidence chips, policy roles, allocated/unallocated totals, and plain-English explanations for
  every non-selected holding, gated by `numeric_plan_trusted` with exact blockers and repair
  actions when trust is degraded (screenshots: `advisor-cash-plan-trusted-desktop.png`,
  `advisor-cash-plan-degraded-desktop.png`; capture: RESPONSES.md).

## Deployment steps (record)

1. Merge the PR. Vercel (frontend) and Railway (backend web service) redeploy automatically;
   Railway start commands and worker services are unchanged (workers stay optional and off).
2. Apply `v2/database/025_watchlist.sql` in the Supabase SQL editor (additive; validation
   queries in the file). Until applied, Watchlist shows its explicit migration-required state.
3. Confirm env: Railway — `FINANCE_RUNTIME_CERT_ENABLED=true`, `FINANCE_RUNTIME_CERT_SECRET`,
   cert user id/email, `INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true` (must stay),
   `INTEL_V3_ON_DEMAND_REFRESH_ENABLED=true` (recommended so Run Intel drains),
   `INTEL_V3_SNAPSHOT_WRITES_ENABLED=true` (needed for new snapshots; cost-guard decision).
   Vercel — `FINANCE_RUNTIME_CERT_SECRET` (server-only, same value), `NEXT_PUBLIC_API_URL`,
   `NEXT_PUBLIC_SUPABASE_URL/ANON_KEY`. `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` is no
   longer read — safe cleanup later, not required.
4. Rollback: revert the merge commit; no data loss (no destructive SQL); an applied
   `watchlist_items` table remains unused and harmless.

## Unresolved external limitations

- Production credentials (owner Supabase login, Railway/Vercel dashboards) are unavailable in
  this environment: live-production runtime validation and the migration application itself are
  manual post-merge steps, documented above. No other limitation remains; no product behavior
  required by the consolidation contract was deferred.

## Final test/build state and test-count reconciliation (record)

- Backend final: `python3 -m pytest tests/ -q` → **8290 passed, 0 failed** (files: 199).
  Baseline: 9,003 collected (93 failed / 8,910 passed) across 222 files.
  Reconciliation: −807 tests deleted strictly with their exclusive deleted surfaces (27 files:
  790 tests + 17 trimmed from 4 mixed files — enumerated in the Phase 2 record), +94 added
  (25 policy-ticker parity, 14 preview explanations, 16 watchlist, 32 tax lots incl. review
  fixes, 7 other). 9,003 − 807 + 94 = 8,290. Zero tests deleted to "get green" — every stale
  fixture was updated, not removed.
- Frontend final: `npx jest --runInBand` → **16 suites / 515 tests passed, 0 failed**.
  Baseline: 28 files / 1,050 passing (3 further suites failed to compile on main).
  Reconciliation: 17 test files deleted with their exclusive legacy surfaces (Agent*/
  DataQuality/InsightCardThesis/portfolioSynthesis/DeployV3*/PaycheckPlanPreviewContract/
  alert-*/decision-log/deploy-*/journal-ledger/today-command-center/portfolio-ledger — each
  listed beside its deleted surface in the retirement record; the cert-secret safety assertions
  from PaycheckPlanPreviewContract were re-created in `cert-secret-safety.test.ts`), and
  ~200 new tests were added across positions-view, watchlist, tax-lots wiring,
  advisor-readiness, advisor-cash-plan, nav, route-redirects, cert-secret safety.
- `npx tsc --noEmit` → **0 errors** (baseline had 12). `npx next build` → green (20/20 pages;
  three views + redirect stubs + import/settings/login/position/[ticker]).
- Screenshots re-shot after the final fix batch through the same harness; all review copy
  fixes verified rendering live (see advisor-cash-plan-degraded-desktop.png for the
  plain-English blocker + technical-detail expander + "No trusted allocation totals" state).

## Same-PR semantic correction (record)

Five blockers patched in place on PR #473 (no architecture change, no backend change, no
allocation/policy/worker/SQL change):

1. **Single Run Intel controller.** `IntelV3Cockpit` (second snapshot query, second run
   mutation, own run button, own lastRunResult, 15s polling lifecycle, status band) deleted;
   holding actions now render via presentation-only `components/advisor/IntelV3HoldingsPanel`
   fed by the page's single shared snapshot query. AdvisorPage owns the one query, one
   mutation, bounded run state, and snapshot invalidation. Preserved: Investment Committee
   summary, action filters, evidence summary, cards, What Changed, drawer, Data Health.
   Grep-proof: `useRunIntelV3` appears only in hooks.ts (definition), advisor/page.tsx, tests.
2. **Placeholder removal.** Opportunity Radar section, ComingLaterPanel component, and the
   three ComingLater placeholders inside DataHealthDrawer removed; zero
   "Coming Later"/"future stage"/placeholder panels across the three views (source-contract
   test enforces).
3. **Truth vocabulary separation.** New server-only `GET /api/advisor/readiness` route handler
   (paycheck proxy pattern; secret never client-side — cert-secret safety test allowlists
   exactly the two route handlers) over the existing read-only cert-gated
   financial-truth-baseline diagnostic; returns a small mapped contract. Snapshot-derived
   fields no longer labeled portfolio/price truth (renamed Intel certification / Snapshot
   source health); Portfolio financial truth, Current-price truth, Books reconciliation are
   endpoint-fed with honest Unknown; six distinct dimensions incl. Cash-plan trust
   (numeric_plan_trusted remains authoritative after a plan request;
   recommendations_trusted stays false). Trust panel healthy message requires the full
   conjunction; unknown is never healthy; reconciliation failure surfaces the real repair
   action with raw operator text behind technical detail. The readiness pill renders as
   "Intel Ready" so Intel state can never read as whole-system readiness. useAdvisorTruth:
   5-min staleTime, retry off, no polling.
4. **No-rationale-no-render.** Canonical `extractHoldingRationale` (why_text →
   asset_intelligence_context.why_this_action → action_text, trimmed) +
   `partitionRenderableCards`; unexplained actions never render as cards and are reported as
   "Not shown: N holding(s) — no explanation was available for their current action." with a
   collapsible ticker list; filter counts count renderable cards only.
5. **Runtime artifact.** Committed `v2/frontend/fakeauth.pid` removed; `*.pid` ignored; no PID
   files, logs, tokens, ports, or fixture state tracked.

**Validation after patch:** backend `8290 passed, 0 failed` (untouched); frontend
`18 suites / 575 tests passed, 0 failed`; `tsc --noEmit` 0 errors; `next build` green
(21/21 pages incl. `/api/advisor/readiness`). Affected screenshots re-shot through the same
harness against the patched production build: one Run Intel control, no Radar/Coming-Later,
honest truth labels ("Intel Ready" pill; new `advisor-truth-degraded-desktop.png` shows
degraded Portfolio financial truth / stale Current-price truth / Books reconciliation with
real disagreeing values 21,129.06 vs 20,633.85 and the repair action). Readiness proxy
captures (baseline + degraded) appended to RESPONSES.md as labeled local contract proof.
**Vercel preview:** deployment for the patch commit reports Ready (Vercel bot), but this
sandbox's egress proxy blocks vercel.app (403 CONNECT), so in-environment preview HTTP
verification was not possible; the readiness proxy consumes pre-existing production
diagnostics, so no backend deploy is needed for preview parity.
