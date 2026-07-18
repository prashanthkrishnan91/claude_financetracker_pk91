# REFACTOR REPORT — Lean Advisor Consolidation (Positions, Advisor, Watchlist)

Status: **Phase 0 — contract and baseline committed BEFORE any production code change.**
Later sections are filled in as phases land; nothing below the Phase-0 line is deleted, only appended/updated.

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
