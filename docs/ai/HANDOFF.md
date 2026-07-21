# HANDOFF — Current Repo State

Last updated: 2026-07-21 (Run Intel distributed workflow — the bounded-drain execution
architecture is replaced by a durable SQL task graph executed by a backend worker
supervisor; the browser only creates one session and polls status. Contract:
`docs/ai/RUN_INTEL_DISTRIBUTED_WORKFLOW.md`; migration
`v2/database/027_intel_run_distributed_tasks.sql`. Deploy Cash work from 2026-07-19
unchanged; consolidation evidence remains in `REFACTOR_REPORT.md`.)

## Product architecture (read this first)

The authenticated app has exactly **three primary views**:

1. **Positions** (`/dashboard/positions`) — certified portfolio truth: totals, allocation split,
   freshness, per-holding detail with Intel v3 action/evidence labels, and
   reconciliation-gated FIFO tax-lot estimates (`GET /api/v1/positions/tax-lots`).
2. **Advisor** (`/dashboard/advisor`) — the **single user-facing recommendation surface**, four
   sections: (A) system readiness + bounded Run Intel, (B) deterministic Intel v3 holding
   actions (`IntelV3Cockpit`), (C) new-cash plan via the canonical Paycheck Advisor endpoint,
   (D) collapsed trust/repair drawer.
3. **Watchlist** (`/dashboard/watchlist`) — user-defined price_below/price_above criteria
   (`/api/v1/watchlist`, table `watchlist_items`, migration `v2/database/025_watchlist.sql`,
   RLS owner policy). The app never auto-selects watchlist stocks and they never enter the
   Paycheck Advisor candidate set.

Operational subpages (not primary nav): `/dashboard/import`, `/settings`,
`/dashboard/position/[ticker]`, login. `/dashboard` redirects to Positions; all legacy product
routes redirect (map in `v2/frontend/src/lib/route-redirects.ts`).

## The decision spine (one spine, no competitors)

1. Certified portfolio/transaction truth (`portfolio_service`, `import_service`, positions)
2. Current-price truth (`price_engine`; repair: `current_price_truth_repair_v1`)
3. Intel v3 evidence production (`services/intelligence/*`; agents/LLMs are **labeled evidence
   producers only**, used by the protected refresh adapters and the cert harness)
4. **Intel v3 deterministic policy** (`intelligence/v3/decision_policy_v1.decide()`) — the only
   owner of visible Buy/Hold/Trim/Sell actions
5. Allocation policy + guardrails (`allocation_policy_v1`: ETF floor 40%, stock sleeve target,
   concentration/group caps, min-trade, VTI>VOO>SPY>QQQ, cash invariants
   `allocated_cash <= cash_to_deploy`, `unallocated_cash >= 0`)
6. **Paycheck Advisor** (`POST /api/v1/advisor/paycheck-plan/preview`, cert-gated via the
   frontend Route Handler that attaches server-only `FINANCE_RUNTIME_CERT_SECRET`) — the
   canonical answer to "I have $X — what should I buy, how much, and why"
7. One Advisor view rendering both

**Intel v3 and Paycheck Advisor are complementary layers, never competitors**: Intel v3 owns
deterministic actions for existing holdings; Paycheck Advisor consumes certified truth +
Intel v3 evidence + allocation policy to place new cash. The preview response carries additive
`explanations` buckets (selected / evidence_eligible_policy_blocked / evidence_blocked /
concentration / group-cap / stale-price / missing-truth / below-min-trade / max-positions)
mapped from the Stage 12C/13A/13C diagnostic — presentation only, no new allocation math.

## Decisions that must NOT be re-litigated (historical context)

- **Do not rebuild a visible LLM/agent recommendation surface.** The legacy
  `recommendation_engine` insight-card path (`GET /recommendations/`, `AgentInsightCard`,
  `PortfolioSynthesisPanel`, flag `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED`) was removed
  in the consolidation because LLM output must never own visible Buy/Hold/Trim/Sell authority
  (`KNOWN_FAILURE_MODES.md`). `recommendation_engine.py` + `services/agents/*` + `services/ai/`
  survive ONLY as internal evidence producers for the Intel v3 refresh adapters and the
  cert harness (`diagnostics.py`) — never re-expose them as a user surface.
- **Do not rebuild a duplicate Deploy surface.** Deploy v3 (`routers/deploy_v3.py`,
  `services/deploy/*`) and the legacy allocation engine chain (`allocation_engine`,
  `deployment_engine`, `adaptive_deployment`, `regime_engine`, deposits schedule with hardcoded
  weights, `decision_engine`, `personalized_decision_engine`, `strategy_engine`,
  `simulation_engine`, decision logs/journal, AI rebalance) were deliberately retired
  (2026-07-18). New-cash sizing belongs to Paycheck Advisor; holding actions to Intel v3.
- **Do not build a separate/new Paycheck model or endpoint family.** The paycheck evolution
  lineage (Stage 12B policy → 12C ETF preference → 12D preview read model → 12E UI → 13A
  evidence-aware stock gating → 13C production `current_holdings` snapshot contract + independent
  policy/evidence gates, PR #471) all lives in `allocation_policy_v1.py` behind the one endpoint.
  Extend it additively there.
- **Do not delete the Advisor allocation spine** (`allocation_policy_v1`,
  `paycheck_plan_preview`) — rejected PR #472 did exactly that and was rejected for it.
- **Deploy Cash refreshes price truth on the explicit click, a stale or missing price is never
  eligible for new cash, and a degraded-but-calculable plan is preserved (not erased)** (product
  recovery, patched same-day to close a release blocker): `POST /advisor/paycheck-plan/preview`
  now runs `current_price_truth_repair_v1.run_current_price_truth_repair(dry_run=False)` for
  stale/missing open-position prices BEFORE `run_next_buy_policy_diagnostic` — permitted only
  because Deploy Cash is an explicit user action (never on page load/polling). The repair's own
  fetch phase runs with bounded concurrency (`_MAX_CONCURRENT_FETCHES=5`, `asyncio.gather` under
  a semaphore) instead of serial per-ticker waits; writes stay sequential and
  price_history-only. The repair is best-effort — an unexpected exception never blocks the plan;
  the response carries an additive `price_truth_repair` summary
  (`refreshed`/`partial`/`unavailable` + counts). **Canonical fix (not a presentation filter):**
  `allocation_policy_v1._compute_gaps` now excludes a ticker from candidacy on either a
  **missing** price (`no_price_available`, unchanged) OR a **stale** price
  (`stale_price_not_eligible_for_new_cash`, new) — a stale ticker can never itself receive new
  cash, and when it's excluded the existing allocator automatically reranks/reallocates to
  another fresh eligible candidate (no new allocator, no duplicated math). Cash bounds,
  concentration rules, evidence gates, and the VTI>VOO>SPY>QQQ preference are unchanged.
  `paycheck_plan_preview.build_paycheck_plan_preview()` (defense-in-depth) independently verifies
  that invariant before preserving a degraded plan — no selected candidate may appear in
  `missing_price_tickers`/`stale_price_tickers` — and suppresses the entire plan (forces
  `status=blocked`, zeroes `allocation_summary`) rather than ever displaying unsafe dollar
  guidance if it is ever violated (e.g. by a future allocator regression or a hand-built
  diagnostic payload); this never re-filters or recomputes the diagnostic's own selection/cash-plan
  math. Only `blocked` (no computable portfolio value, reconciliation beyond tolerance, no safe
  candidate prices, or the defense-in-depth invariant firing) empties the plan — `degraded`
  (non-fatal residual price/provider limitations on some OTHER, non-candidate holding) preserves
  the diagnostic's own calculated candidates verbatim, keeping `trusted=false`/`status=degraded`
  and the existing caveats. No new allocator, no policy/cap loosening, no fabricated candidates;
  the decision spine (certified truth → repaired price truth → Intel v3 evidence →
  `allocation_policy_v1` → `paycheck_plan_preview` → Advisor cash-plan section) is unchanged.
- **Run Intel is a durable distributed task graph** (contract:
  `docs/ai/RUN_INTEL_DISTRIBUTED_WORKFLOW.md`; migration
  `v2/database/027_intel_run_distributed_tasks.sql`; replaces the bounded-drain
  session flow, the browser continuation loop, and any Run Intel path through
  `run_analyst_refresh_only()`):
  * Control plane: `POST /intel/v3/run` (browser-minted UUID per click) freezes the
    portfolio scope into `intel_run_tickers` (one row per ACTIVE holding — batch size can
    never redefine run scope), seeds `intel_run_tasks` (generic durable queue: leases,
    SKIP LOCKED claim RPC `claim_intel_run_tasks` + CAS fallback, idempotent logical task
    identity), activates the in-process worker supervisor, returns fast. Zero provider/LLM/
    policy/snapshot work in-request. One active session per user (partial unique index);
    a click during an active run adopts it.
  * Status plane: `GET /intel/v3/sessions/{id}/status` + `/sessions/active` — read-only,
    plain-English `plain_status`; polling observes work, never performs it. Page close
    never stops the run; returning rediscovers the active session.
  * Worker: `distributed/worker_supervisor_v1` (in-process asyncio; started by /run,
    app-startup crash recovery; exits when idle — no polling cost). Scheduler
    (`run_scheduler_v1`) creates dependency waves: ticker-scoped lane collectors →
    immutable evidence bundles → asset-compatible specialist batches (≤5, global LLM
    semaphore) → deterministic conflict-triggered review → per-ticker deterministic
    decision → portfolio join + certified publication (reuses `run_prewarm_snapshot`,
    one session-linked snapshot, publication-only retry). Failure isolation: lane →
    lane, specialist → batch/axis, ticker → ticker (`no_call` EVIDENCE INCOMPLETE, never
    fabricated verdict rows); session terminal states `completed` /
    `completed_with_gaps` / `failed`.
  * Collectors reuse existing providers (data_sources fetchers + breakers/semaphores,
    SEC/ETF/macro research-worker runners writing `research_artifacts`); lane TTL reuse +
    specialist `input_fingerprint` reuse skip duplicate provider/LLM work. Specialists are
    pure (bundle-only input, `LLMClient.ask_json`, strict JSON, one repair retry,
    per-(ticker,axis) rows in `intel_run_specialist_outputs`). Deterministic
    `decision_policy_v1.decide()` remains the only visible action authority; specialist
    scores aggregate into an advisory signal only.
  * Retired: `intel_run_session_flow_v1`, `analyst_refresh_on_demand_drain_v1`, the
    browser auto-continuation in `useRunIntelV3`, session-scoped
    `analyst_refresh_jobs` enqueue. Unfinished legacy (workflow_version=1) sessions were
    marked `superseded` by migration 027. The legacy analyst-refresh worker Railway
    service remains only for the flag-off background path and can never see distributed
    sessions or claim session-linked jobs.
  * Tests: `test_distributed_architecture_boundary.py` (static import/symbol fences),
    `test_distributed_run_creation.py` (34-ticker fast create, forbidden seams),
    `test_distributed_sql_contract.py` (migration 027 + retention),
    `test_distributed_collectors_and_store.py` (ticker scoping, lane isolation, TTL,
    atomic claims/leases/double-completion), `test_distributed_specialists_and_review.py`,
    `test_distributed_decision_and_publication.py` (deterministic authority, NO CALL,
    publication-only retry), `test_distributed_golden_run.py` (34-holding golden run with
    exact provider/LLM accounting + 83f28044-shaped 32-ticker regression).
- **Cost guard posture stays** (ACTIVE): `INTEL_BACKGROUND_WORKERS_ENABLED=false` master kill
  switch, `INTEL_V3_SNAPSHOT_WRITES_ENABLED` write guard, interval clamps. Do not re-enable
  background workers casually; see `docs/deploy/RAILWAY_COST_GUARD.md`.
- **Truth/repair diagnostics are protected operator infrastructure** (Stage 10B/11A/11B/12C
  lineage): `/diagnostics/finance-intel/*` (~36 cert-gated endpoints incl. financial-truth
  baseline, books reconciliation, current-price repair, next-buy diagnostic). They are not
  primary navigation and must not be deleted for looking unused.
- **Policy tickers live in config** (`v2/backend/app/policy_tickers.json`, loader
  `services/policy_tickers.py`, override `POLICY_TICKERS_FILE`) with exact-parity tests
  (`test_policy_tickers.py`). Never re-hardcode ticker membership in policy modules; provider
  symbol-translation maps stay in provider code.
- **Tax lots are estimates, reconciliation-gated** (`tax_lot_engine.py`): every production
  tx_type explicitly classified; unsupported/unknown share events block authoritative display;
  calendar-anniversary long-term logic; shares tolerance max(0.0001, 0.1%), basis 2%;
  no dollar tax-liability math anywhere; US-federal estimates-only labeling.
- **`recommendations_trusted` is always False** in the preview contract; `numeric_plan_trusted`
  gates investable presentation. Never render an untrusted plan as actionable.

## Current test/build state (post-consolidation)

- Backend: full suite green (`8290 passed, 0 failed` at consolidation; includes the conftest
  event-loop guard and stale-fixture modernization — both test-only).
- Frontend: full jest green; `tsc --noEmit` clean; `next build` green.
- Baseline before consolidation (main @ PR #471): backend 93 failed / 8910 passed
  (documented pre-existing failures), frontend 3 suites failing to compile.
- Distributed Run Intel workflow PR (2026-07-21): full backend suite green (8400+ passed,
  0 failed — Tier 3: broad architecture/schema change + mission-mandated), full frontend
  jest green (596 passed), `tsc --noEmit` clean, `next build` green (placeholder
  `NEXT_PUBLIC_*` env vars needed to prerender locally — sandbox-only).

## SQL / env state

- Migration `v2/database/027_intel_run_distributed_tasks.sql` is REQUIRED (manual,
  idempotent) for the distributed Run Intel workflow; until applied, POST /intel/v3/run
  returns an explicit retryable `run_session_create_failed` (no legacy fallback). It also
  marks unfinished legacy sessions `superseded`. Optional tuning env vars (defaults in
  code, no manual action): `INTEL_V3_DISTRIBUTED_MAX_COLLECTOR_CONCURRENCY=4`,
  `INTEL_V3_DISTRIBUTED_MAX_LLM_CONCURRENCY=2`,
  `INTEL_V3_DISTRIBUTED_MAX_SPECIALIST_BATCH=5`,
  `INTEL_V3_DISTRIBUTED_TASK_LEASE_SECONDS=300`,
  `INTEL_V3_DISTRIBUTED_MAX_TASK_ATTEMPTS=3`.
  `INTEL_V3_ON_DEMAND_REFRESH_ENABLED` no longer affects Run Intel (kept only so set
  deployments don't fail validation).
- Migration `v2/database/025_watchlist.sql` is REQUIRED (manual, additive) for Watchlist;
  endpoints return 503 `watchlist_migration_required` until applied. Everything else unchanged.
- `FINANCE_RUNTIME_CERT_SECRET` (Vercel, server-only) + `FINANCE_RUNTIME_CERT_ENABLED=true`
  and cert user config (Railway) power the Advisor cash plan.
- `INTEL_V3_ON_DEMAND_REFRESH_ENABLED=true` (Railway) recommended so Run Intel drains without
  the optional worker. `INTEL_V3_SNAPSHOT_WRITES_ENABLED=true` needed for new snapshots.
- Backend `INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true` (Railway) MUST stay set — the Advisor view's
  Intel section reads `GET /intel/v3/snapshot`, which 404s without it. Only the frontend
  `NEXT_PUBLIC_...` variant of this name is dead. Backend boot-required vars remain
  `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`,
  `ENCRYPTION_KEY`.
- `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` is no longer read by any code — safe to
  remove from Vercel at leisure (documented cleanup, not required).
