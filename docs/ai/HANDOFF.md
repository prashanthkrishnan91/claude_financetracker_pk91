# HANDOFF — Current Repo State

Last updated: 2026-07-21 (Run Intel durable sessions — recovery replacing PR #480's approach.
One manual Run Intel click now owns one SQL-backed `intel_run_sessions` row (browser-minted
UUID, migration `v2/database/026_intel_run_sessions.sql`); session jobs and the published
snapshot are FK-linked to that exact session, and the production ticker-refresh path executes
`AgentOrchestrator.run_analyst_refresh_only()` — it can no longer reach portfolio synthesis,
which was consuming the request deadline after per-ticker analysis had already succeeded and
blanket-failing the batch. Deploy Cash work from 2026-07-19 unchanged; consolidation evidence
remains in `REFACTOR_REPORT.md`.)

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
- **Run Intel is one durable SQL-backed session per click** (fix/run-intel-durable-sessions,
  replacing the pre-session Stage 13B router augmentation and PR #480's rejected approaches —
  no sentinel tickers, no window-as-identity, no completion inferred from the latest snapshot):
  * Identity: the browser mints ONE UUID per manual click (`crypto.randomUUID()` in
    `useRunIntelV3`); every bounded automatic continuation POSTs the same
    `{"run_session_id"}` body; a later click always mints a different id. Legacy body-less
    callers get a backend-minted id (`RunIntelV3Request`).
  * Durable state: `public.intel_run_sessions` (migration
    `v2/database/026_intel_run_sessions.sql`, service-role/deny-all RLS) stores status
    (`created → ticker_refresh_in_progress → publishing → completed`, plus
    `publication_retryable_failed` and terminal `failed`), the immutable holdings scope +
    stale subset captured at click time, expected job count, pre-session snapshot row id,
    completed snapshot row id, and retryable error info. `analyst_refresh_jobs.run_session_id`
    and `intel_v3_snapshots.run_session_id` are nullable FKs; session jobs are unique per
    `(run_session_id, ticker)`; legacy NULL-session rows keep the old
    `(user_id, ticker, refresh_window)` uniqueness; at most one snapshot row per session
    (publication idempotency index).
  * Flow (`intel_run_session_flow_v1.run_intel_session_request`, called by the router):
    first use of an id captures scope + enqueues one session job per stale ticker
    (`enqueue_session_jobs`); continuations verify ownership (403 on mismatch), credit
    interrupted-but-persisted tickers from durable evidence (never regenerate), lift worker
    backoff for the click's own jobs, drain ONE bounded batch (1 × 3 jobs ≤ 20s via
    `run_on_demand_drain` scoped by `run_session_id`, prewarm disabled), and when every
    session job has succeeded run deterministic certification over the session's immutable
    scope (`check_certified_intel_run_contract(scope_tickers=…)`) and publish ONE snapshot
    carrying the session id in both the SQL column and the payload
    (`run_prewarm_snapshot(run_session_id=…)` → `_persist_snapshot` returns the row id).
    Publication failures keep every ticker job succeeded and retry publication only.
    Completion is reported ONLY after re-verifying the session's own snapshot row (column +
    payload linkage, differs from pre-session snapshot, `worker_certified`,
    `certified_current`, zero unfinished session jobs).
  * Analyst-only execution (the production fix): the default adapter backend
    (`full_portfolio_analyst_refresh_adapter_v1.default_full_portfolio_agent_orchestrator_backend`)
    calls `AgentOrchestrator.run_analyst_refresh_only(run_id, tickers=…)` — the real market/
    context/snapshot/feature/analyst/persist stages with a hard structural stop before
    Phase 4. It never invokes `_run_portfolio_synthesis` / narrative / allocation synthesis;
    the full `AgentOrchestrator.run()` pipeline is untouched for other product features.
    Root cause this fixes: production ticker analysis succeeded, then unconditional
    portfolio synthesis consumed the remaining deadline, the adapter timed out, and the whole
    batch was reported failed.
  * Frontend: still exactly one button; `deriveRunJobs` prefers the explicit session fields
    (`expected_ticker_count`/`session_succeeded_ticker_count`/`session_remaining_ticker_count`)
    over per-request batch reconstruction; continuation/caps/abort behavior unchanged
    (`RUN_INTEL_MAX_CONTINUATIONS=20` honestly covers ceil(32/3)+publication).
  * Tests: `test_run_intel_session_sql_contract.py` (migration contract),
    `test_run_intel_analyst_only_production_path.py` (real adapter seam with `run()` +
    `_run_portfolio_synthesis` patched to raise, revert simulation, timeout regression),
    `test_run_intel_session_flow.py` (isolation / 16-ticker interruption-resume /
    publication-only retry / 32-ticker exact accounting over the REAL store+worker),
    `test_run_intel_session_router.py` (endpoint seam; replaces the deleted
    `test_stage13b_run_intel_on_demand_status.py`, whose router-augmentation architecture
    was removed).
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
- Advisor product-recovery PR (2026-07-19, initial + same-day release-blocker patch): focused
  Tier 1 bundle (Intel v3 / paycheck / allocation-policy / price-truth / run-intel / deploy-cash /
  advisor tests) — `3119 passed, 0 failed` backend; full frontend jest `608 passed, 0 failed`;
  `tsc --noEmit` clean; `next build` green (locally requires placeholder
  `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY` env vars to prerender — no code path
  change, sandbox-only). Full backend suite not run (Tier 3 criteria not met; see PR body
  test-tier justification).

## SQL / env state

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
