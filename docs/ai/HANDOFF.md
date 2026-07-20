# HANDOFF — Current Repo State

Last updated: 2026-07-20 (Run Intel durable session identity. Every explicit Run Intel click now
carries one durable `run_session_id` spanning enqueue → queue count/claim → bounded analyst
batches → automatic continuations → interruption/resume → certification/publication retry →
snapshot linkage → completion. Migration-free: the session UUID is stored in the EXISTING
`analyst_refresh_jobs.refresh_window` text column (`refresh_window == run_session_id`), no new
column/table/SQL. `count_due_jobs`/`claim_due_jobs` gained an optional `run_session_id` filter;
`AnalystRefreshWorker` gained `scope_session_id`, surfaces `run_session_id` on every
`WorkerRunResult`, and gained a publication-only retry (zero ticker analyst calls, zero synthesis)
for a same-session snapshot-publish failure; `run_on_demand_drain` threads the session; the router
mints a session on the initial click (a same-day second manual action gets a distinct one),
reuses the in-flight session across continuations via a non-claimable session-anchor row, and
gates completion on a snapshot whose payload embeds the same `run_session_id` — a historical
certified snapshot can never complete a new session. `POST /intel/v3/run` accepts an optional
`{run_session_id}` body and returns `run_session_id`; the `useRunIntelV3` hook stores the click's
id and sends it on every continuation, clearing it before a later manual click. Run Intel still
runs NO portfolio synthesis in its critical path. Acceptance: immutable
`test_run_intel_session_contract_v1.py` (10/10) unchanged; +`test_run_intel_session_production_v1.py`
(7). Full backend 8383 passed; frontend jest 609 passed; tsc + build green. Live Railway/Supabase
validation still outstanding. Full evidence for the earlier consolidation lives in
`REFACTOR_REPORT.md` at the repo root.)

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
- **Run Intel is bounded on-demand, not worker-dependent, and self-continues** (Stage 13B +
  product recovery): `POST /intel/v3/run` enqueues and, when
  `INTEL_V3_ON_DEMAND_REFRESH_ENABLED=true`, drains a small quantum
  (`analyst_refresh_on_demand_drain_v1`: 1 batch × 3 jobs, ≤20s) per request — small enough that
  one request can never materially exceed a production-safe wall-clock bound even if every
  selected ticker's LLM call runs to the deadline. The drain's own bound is threaded into
  `AnalystRefreshWorker`'s `max_adapter_seconds`, which clamps the analyst-refresh adapter's own
  `wait_for()` budget — the prior gap (`FullPortfolioAnalystRefreshBudget` kept its 180s default
  regardless of the caller's intended cap) is what let a nominal 90s cap become a ~148s hung
  request in production. The router (`_augment_with_on_demand_status`) no longer decides whether
  to drain solely from `queued_ticker_count`. Same-day patch (release blocker): the router now
  ALWAYS resolves the current user's current active tickers first — via
  `IntelV3Service._get_active_tickers()` — before any drain decision, including when this click
  DID queue new work (previously only the zero-queued path fetched them). If that lookup itself
  fails, the router never falls back to an unscoped drain; it returns an explicit retryable
  failure (`_ACTION_ACTIVE_TICKERS_LOOKUP_FAILED`) instead. It then classifies the FULL durable-job
  state for (user, active_tickers) via `analyst_refresh_job_store_v1.count_due_jobs`'s breakdown —
  not just `total_due`: `total_due > 0` drains; only-`failed_not_yet_due` (backoff, nothing due
  yet) reports an explicit backoff retry state and stops automatic continuation cleanly; any
  `failed_terminal` (retry budget exhausted) reports a terminal analyst-job failure and never
  auto-loops; only when none of these apply does the existing zero-work/current-snapshot logic
  run. Both `total_due`-driven and existing-work-driven drains scope claiming to the current user
  AND their current active tickers (`AnalystRefreshWorker.scope_user_id`/`scope_tickers`;
  `claim_due_jobs`/`count_due_jobs` gained optional `user_id`/`tickers` filters) — an obsolete job
  for a sold/closed ticker is never claimed by the manual Run Intel path, even though the
  standalone always-on worker keeps its unscoped global-queue behavior unchanged.
  `count_due_jobs` also gained a small extension, `earliest_retry_at` (earliest `next_retry_at`
  among backoff rows), surfaced as an additive `earliest_retry_at` response field only in the
  backoff state. A historical certified-current snapshot can never mask a backoff or terminal
  durable-job state — both force `snapshot_available_after_run=False` unconditionally, ahead of
  the drain/zero-queued-success checks. Otherwise, `snapshot_available_after_run` keys off
  `drain_ran` (true whenever a `total_due`-driven drain ran) rather than `queued_ticker_count > 0`:
  completion still requires proof THIS request published (on-demand enabled, nothing left
  resumable, writes enabled, latest snapshot `worker_certified` + `certified_current` with a
  `snapshot_id` different from `existing_certified_snapshot_id`); a zero-queued no-op success
  status (`analyst_evidence_current` / `*_contract_recertified`) with no drain still means
  "nothing to do"; anything else (no-holdings, enqueue/recert failure, or a drain that ran but
  never produced a new snapshot) is an honest retry, never a false "nothing was stale."
  Frontend: `useRunIntelV3` (`hooks.ts`) now drives bounded automatic continuation from the SAME
  click — after each batch it derives the run state via `shouldAutoContinueRun`
  (`advisor-readiness.ts`) and, while `partial`, fires another request itself (capped at
  `RUN_INTEL_MAX_CONTINUATIONS=20` attempts / `RUN_INTEL_MAX_ELAPSED_MS=120s`), aborting in-flight
  work via `AbortController` on unmount. The user never has to click "Continue Intel run"
  themselves. `advisor-readiness.ts::deriveRunModel` gained three narrow explicit branches for the
  new backoff/terminal/active-ticker-lookup-failure `next_required_action` values — all classify
  as the existing `failed` state (Retry Intel run control, `buttonBusy=false`), placed ahead of
  the generic `reclick_`-prefix "partial" bucket so they can never be misread as auto-continuing
  progress; `shouldAutoContinueRun` naturally stops for `failed` (only continues on `partial`). No
  new button/control. The rest of the state machine (partial/complete/failed/
  queue_only classification, including any `status` ending in `_recertification_failed`) is
  unchanged — only the trigger for firing the next request moved from a manual click to the hook.
  Still exactly one control — `AdvisorReadinessPanel`/`page.tsx` are unchanged.
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
