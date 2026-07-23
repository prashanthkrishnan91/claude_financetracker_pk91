# HANDOFF — Current Repo State

Last updated: 2026-07-23 (Run Intel trust contract PR 1/7 — production session
`a51e977b-561a-4e98-baa8-59ad56a877ff` audit found 31/31 decided but 0 evidence
bundles/specialist outputs with source references, 5/7 required conflict reviews
failed silently, `research_axis_readiness={}` mislabeled successful technical/
sentiment outputs as unusable, and every nonempty decision blocker rendered as
"Evidence blocked" regardless of category. See the locked seven-PR sequence
below — this entry is PR 1.)

Previously (2026-07-22, Haiku specialist output completion fix, PR #484 — production
failure: session 7c4069a1-cc07-4c1e-a7d4-3bea67dd206d froze 31 holdings but completed 14
decided / 17 NO CALL / 22 terminal task failures because Haiku returned verbose/Markdown-
fenced/truncated JSON at 5-ticker batches with an unbounded ~350 tokens/ticker budget, and
the whole batch retried through the durable task's full attempt budget instead of
repairing just the missing tickers. Release-blocker follow-up on the same PR:
`LLMClient.ask_json()`'s own internal same-model truncation retry was still silently
doubling an already-bounded specialist batch call, invisible to the specialist's call
count and the 1800-token ceiling — `ask_json()` gained `retry_truncated_response` (default
True; specialist calls pass False), and the quota/auth-only repair skip was widened to
any actual provider-call failure (rate-limit/transient included) so it's never confused
with a ticker-level JSON parse failure. Fix landed entirely inside the existing specialist
execution seam — no Run Intel architecture, collector, decision-policy, publication, or
frontend change. Same-day: distributed Run Intel model cost routing — standard specialist
analysis moved to Haiku 4.5 with no Sonnet escalation, conditional conflict review stays
on Sonnet 5 with a Haiku fallback; migration 027's owner-guard trigger variable bug
corrected. 2026-07-21: Run Intel distributed workflow — the bounded-drain execution
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

## Run Intel trust-recovery sequence (LOCKED — active scope)

Seven-PR sequence restoring truthful Run Intel trust after the 2026-07-23
production audit of session `a51e977b-561a-4e98-baa8-59ad56a877ff` (31 frozen
holdings, 31 persisted decisions, session `completed` — but 0 evidence
bundles/specialist outputs carried a source reference, 5 of 7 required
conflict reviews failed with no visible trace, distributed publication wrote
`research_axis_readiness={}` so 31/31 successful technical and 31/31
successful sentiment outputs displayed as unusable, and the UI collapsed
every nonempty decision blocker into "Evidence blocked" regardless of
category — including a merely-suppressed price context and a portfolio-
overweight constraint). This sequence is the active scope for Run Intel work
until production certification passes; do not start unrelated Run Intel
slices ahead of it.

1. **Publish and display a truthful Run Intel trust contract — IN PROGRESS —
   PR #485.** Becomes COMPLETED only after merge. New pure projection
   `run_trust_contract_v1`
   (`v2/backend/app/services/intelligence/v3/distributed/run_trust_contract_v1.py`):
   session coverage; per-axis specialist coverage split into **required vs.
   optional** per asset type (`task_contracts_v1.REQUIRED_AXES_BY_ASSET` /
   `OPTIONAL_AXES_BY_ASSET`), each succeeded/missing/failed/not_applicable —
   a valid persisted specialist output (`score` and `confidence` both
   present) is the only proof an axis succeeded; a terminal task
   (`SUCCEEDED`/`DEGRADED`) without one counts as failed, never succeeded;
   conflict-review coverage that requires BOTH a successfully-terminal review
   task AND a valid persisted `axis="review"` output — `TASK_DEGRADED` is
   never review success merely for being terminal — with explicit
   not_required/succeeded/failed/pending states, `failed` and `pending` both
   blocking trust and `is_source_validated`; source lineage as
   full/partial/missing per ticker, computed over **every** output that fed
   `aggregate_advisory_signal()` (including the review axis when present),
   not one arbitrary axis; and a deterministic decision-constraint classifier
   (evidence_quality / source_lineage / price_context / portfolio_policy /
   risk / conflict_review / other — non-exclusive) that no longer conflates
   UNDERWEIGHT (room to add) with a portfolio-policy limitation, distinguishes
   SUPPRESSED price context (unconfirmed) from assessed FULL/EXPENSIVE
   valuation states, and preserves an `other` category for any real persisted
   blocker text that doesn't match a known category instead of silently
   dropping it. A per-ticker `trust_status`
   (healthy/limited/blocked/unknown) is derived from these same required-axis/
   review/lineage facts — never a separate heuristic — and an overall
   `overall_status` that can only be `healthy` when every required axis,
   every required review, and full decision-influencing lineage pass; any
   required gap forces `blocked`; only optional gaps or partial lineage
   produce `limited`. Wired into BOTH session-native publication
   (`session_publication_v1.py`, persisted on `payload.run_trust_contract`)
   AND a read-time, **fail-closed** enrichment of pre-existing snapshots
   (`intel_v3_service._enrich_snapshot_with_run_trust_contract`, keyed off
   `run_session_id`, zero provider/LLM calls): when the session, ticker rows,
   or task rows can't be read (missing, empty, or a raised exception), the
   enrichment applies an explicit `unknown`/`pending` trust overlay
   (`_apply_unknown_trust_overlay`) instead of ever preserving a stale
   optimistic `source_validated`/committee status — old cards flip to
   explicitly `unknown`, they never stay silently "healthy" on a failed read.
   `research_axis_readiness={}` placeholder replaced with real per-axis
   readiness; `snapshot_builder._build_source_pack_status` now requires full
   source lineage AND a non-failed, non-pending review status before
   "source_validated" when lineage/review info is available (distributed
   sessions), preserving legacy evidence-band-only behavior when it isn't
   (non-distributed callers). Frontend: `AdvisorReadinessPanel` shows an
   independent "Analysis trust" status (healthy/limited/blocked/
   not_applicable/unknown) plus session/axis/conflict-review/source-lineage
   summary lines, separate from the renamed "Holdings decided" coverage
   metric; `IntelV3Drawer` gained a "What's limiting this holding" section
   listing each decision-constraint category separately, using
   `decision_bands`-aware wording so SUPPRESSED/FULL/EXPENSIVE price context
   read as distinct, accurate states rather than one generic "isn't
   confirmed" claim; `IntelV3HoldingsPanel`'s evidence band keeps its
   existing technical/sentiment `axis_coverage` chips unchanged, but the
   portfolio "better supported / evidence limited / data issues" counts now
   derive directly from backend per-ticker `trust_status`
   (`buildPortfolioEvidenceSummary`) instead of a second frontend safety
   heuristic, so the summary, holding cards, and drawer agree on one backend
   trust state; `buildSafetyDisplay` no longer treats every nonempty blocker
   as "Evidence blocked" — only an actual `evidence_quality` constraint is.
   Financial truth rows (`portfolio_financial_truth`/`current_price_truth`/
   `books_reconciliation`) are untouched — still sourced only from the
   existing `/api/advisor/readiness` truth endpoint. No SQL. **PR 2 still
   owns reference generation** — this PR does not generate any
   `evidence_refs`/`source_refs`, it only reports lineage truthfully against
   whatever already exists (currently "0 of N" in production). **Runtime
   caveat:** fixture and unit/integration test validation is complete, but
   production verification of the historical-session, fail-closed enrichment
   path (a real read against the existing production session with a stale/
   missing task graph) is still required after deployment — it has not yet
   been exercised against live production data.
2. Source quality / source-reference generation — NOT STARTED. Owns making
   `evidence_bundle.source_refs` / specialist `evidence_refs` actually
   nonempty; PR 1's lineage fields will then reflect real coverage instead of
   the current honest "0 of N" state.
3. Conflict-review reliability — NOT STARTED. Owns why 5 of 7 reviews failed
   (prompts, model routing, retry behavior) — PR 1 explicitly does not touch
   review prompts/routing/retries, only surfaces the failures truthfully.
4. Currency normalization — NOT STARTED.
5. Financial-truth refresh — NOT STARTED.
6. Repeat-run reliability — NOT STARTED.
7. Performance — NOT STARTED.

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
  * Worker: `distributed/worker_supervisor_v1` (in-process asyncio; started by /run
    AND a startup probe that survives transient boot-time DB failures and exits only
    after a successful zero-session query; DB outages are never treated as idle —
    bounded backoff + jitter, zero provider/LLM work). Scheduler (`run_scheduler_v1`)
    creates dependency waves: ticker-scoped lane collectors → immutable evidence
    bundles → asset-compatible specialist batches (≤5, global LLM semaphore) →
    deterministic conflict-triggered review → per-ticker deterministic decision →
    session-native portfolio join + publication. Failure isolation: lane → lane,
    specialist → batch/axis, ticker → ticker (`no_call` EVIDENCE INCOMPLETE, never
    fabricated verdict rows); session terminal states `completed` /
    `completed_with_gaps` / `failed`.
  * ONE decision authority: `decide()` runs exactly once per ticker inside the
    decision task; the complete input+output persist on `intel_run_tickers.decision`;
    compat rows (`agent_runs`/`agent_insights`/`recommendations`) are written AFTER
    with the final action (projections, not advisory inputs). Publication
    (`session_publication_v1`) rebuilds cards verbatim from persisted decisions —
    zero `decide()` calls, zero global-recommendation reads (test-fenced), full
    frozen-scope accounting (decided / NO CALL / failed with plain-English gaps),
    distributed certification (card action == persisted action, no foreign/stale
    cards), snapshot_source `worker_certified` vs `worker_certified_with_gaps`
    (non-green amber in UI). Task graph is fail-closed (get_or_create with verified
    duplicates only, expected-graph verification before created→running,
    supervisor-driven repair of every partial-create shape) and claim-token fenced
    (fresh token per claim; completion + every side-effect write require current
    ownership; stale reclaimed workers cannot mutate outputs).
  * Collectors reuse existing providers (data_sources fetchers + breakers/semaphores,
    SEC/ETF/macro research-worker runners writing `research_artifacts`); lane TTL reuse +
    specialist `input_fingerprint` reuse skip duplicate provider/LLM work (reuse ignores
    the producing model — a Sonnet-era output stays reusable under Haiku routing).
    Specialists are pure (bundle-only input, `LLMClient.ask_json`, strict compact JSON —
    no markdown/fences/commentary, ≤2 key_findings/risks/missing_evidence/limitations,
    ~120 chars/string, no visible action word), per-(ticker,axis) rows in
    `intel_run_specialist_outputs`. Normal specialist batches are capped at
    `INTEL_V3_DISTRIBUTED_HAIKU_MAX_SPECIALIST_BATCH` (default 2, independent of the
    unrelated architectural `INTEL_V3_DISTRIBUTED_MAX_SPECIALIST_BATCH=5` ceiling for
    other models) with a bounded 650-tokens/ticker output budget (min 700, max 1800/call).
    A batch call's missing/malformed tickers are repaired ONE TICKER PER CALL — never a
    validated peer — bounding a 2-ticker batch to ≤3 total LLM calls (1 initial + ≤2
    individual repairs) instead of retrying the whole durable task 3×. A quota/
    authentication provider error makes exactly one call, skips repair, and returns
    `TASK_FAILED_RETRYABLE` without ever discarding an already-persisted peer ticker.
    Deterministic `decision_policy_v1.decide()` remains the only visible action authority;
    specialist scores aggregate into an advisory signal only.
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
- Distributed Run Intel model cost routing PR (2026-07-22): full backend suite green
  (8467 passed, 0 failed, includes 16 new focused tests); no frontend files changed.
- Haiku specialist output completion fix PR #484 (2026-07-22): full backend suite green
  (8500 passed, 0 failed, includes 33 new focused tests in
  `test_specialist_output_completion_v1.py` — 27 from the initial patch, 4 from the first
  release-blocker follow-up (counts actual provider requests against a real `LLMClient`),
  2 from the second release-blocker follow-up (`primary_max_attempts=1` — a rate-limit/
  transient failure now costs exactly one real `_single_call()`, not up to 4); the
  34-holding golden run's exact LLM-call accounting moved from 22 to 49 calls to reflect
  the new default 2-ticker Haiku batch cap, same complete coverage); no frontend files
  changed; no SQL.
- Run Intel trust contract PR 1/7 (2026-07-23): full backend suite green (8521 passed,
  0 failed — Tier 3, broad cross-cutting change: `run_trust_contract_v1` is a new
  shared projection consumed by both session-native publication and the shared
  `snapshot_builder.py`/`intel_v3_service.py` read path used by every Intel v3
  snapshot read, plus 33 new focused tests in `test_run_trust_contract_v1.py` and
  `test_run_trust_contract_integration.py`); full frontend jest green (639 passed,
  25 new), `tsc --noEmit` clean, `next build` green (same placeholder
  `NEXT_PUBLIC_*` env vars as prior PRs needed to prerender locally — sandbox-only).
  No SQL.

## SQL / env state

- Migration `v2/database/027_intel_run_distributed_tasks.sql` is REQUIRED (manual,
  idempotent) for the distributed Run Intel workflow; until applied, POST /intel/v3/run
  returns an explicit retryable `run_session_create_failed` (no legacy fallback). It also
  marks unfinished legacy sessions `superseded`. Optional tuning env vars (defaults in
  code, no manual action): `INTEL_V3_DISTRIBUTED_MAX_COLLECTOR_CONCURRENCY=4`,
  `INTEL_V3_DISTRIBUTED_MAX_LLM_CONCURRENCY=2`,
  `INTEL_V3_DISTRIBUTED_MAX_SPECIALIST_BATCH=5`,
  `INTEL_V3_DISTRIBUTED_HAIKU_MAX_SPECIALIST_BATCH=2` (narrows Haiku-routed specialist
  batches further; never exceeds the max-specialist-batch ceiling above),
  `INTEL_V3_DISTRIBUTED_TASK_LEASE_SECONDS=300`,
  `INTEL_V3_DISTRIBUTED_MAX_TASK_ATTEMPTS=3`.
  `INTEL_V3_ON_DEMAND_REFRESH_ENABLED` no longer affects Run Intel (kept only so set
  deployments don't fail validation). Migration 027's two owner-guard trigger functions
  were corrected in place (source-file fix, not a new migration): the PL/pgSQL variable
  `session_user` shadowed the reserved `SESSION_USER` builtin instead of holding the
  local lookup, so the owner comparison silently checked the wrong value; renamed to
  `v_session_user_id` with qualified table aliases and `IS DISTINCT FROM`.
- Distributed Run Intel model cost routing (2026-07-22): `WorkerSupervisor` now builds
  two separate `LLMClient` instances instead of one shared client — standard specialist
  analysis (`TASK_SPECIALIST_ANALYSIS`) routes to `intel_v3_distributed_specialist_model`
  (default `claude-haiku-4-5-20251001`) with fallback disabled (a specialist failure
  retries the durable task on the same model, never auto-escalating to Sonnet); the
  conditional conflict-review agent (`TASK_REVIEW_CONFLICT`) routes to
  `intel_v3_distributed_review_model` (default `claude-sonnet-5`) with fallback to
  `intel_v3_distributed_review_fallback_model` (default `claude-haiku-4-5-20251001`).
  `decision_policy_v1.decide()` remains the only visible Buy/Hold/Trim/Sell authority;
  `TASK_TICKER_DECISION`/publication still make zero LLM calls. `LLMClient.fallback_model`
  is now `Optional[str]` — no fallback when null/empty/identical to the primary model;
  unrelated legacy `LLMClient()` callers (orchestrator, etc.) keep their Sonnet 4.6 →
  Haiku 4.5 default failover unchanged. Env vars (all additive, existing deployments
  unaffected without setting them): `INTEL_V3_DISTRIBUTED_SPECIALIST_MODEL`,
  `INTEL_V3_DISTRIBUTED_REVIEW_MODEL`, `INTEL_V3_DISTRIBUTED_REVIEW_FALLBACK_MODEL`.
- Haiku specialist output completion fix (2026-07-22, no SQL): root cause was an unbounded
  token budget (`350 * batch_size`) combined with 5-ticker Haiku batches and a single
  batched repair retry that re-requested already-valid tickers — Haiku's verbose/fenced/
  truncated responses then exhausted the durable task's whole 3-attempt budget on tickers
  that had already succeeded. `specialist_agents_v1.py`: `_specialist_token_budget()`
  replaces the bare multiplier (650/ticker, clamped 700–1800); the repair loop now issues
  one call PER missing/malformed ticker (never a validated peer), bounding a 2-ticker batch
  to ≤3 total calls; `SpecialistBatchOutcome` gained `repair_calls`/`truncated_calls`/
  `quota_or_auth_failures`/`requested_tickers`/`partial_success` for observability.
  `agents/llm.py`: new `_classify_provider_exception()` (quota/authentication/rate_limit/
  transient) — quota/auth stop the same-model backoff loop after one attempt (a configured
  fallback MODEL, e.g. the review agent's Sonnet→Haiku, still runs — only same-model
  retries and specialist repair calls are skipped); new `_extract_json(..., reject_prose=)`
  strict mode (trim + strip one outer fence, no prose-object scanning) used only by
  specialist calls via `ask_json(..., reject_prose=True)` — the default prose-tolerant
  behavior for all other `ask_json` callers is unchanged. `worker_supervisor_v1.py`:
  `_effective_specialist_batch_cap()` applies `intel_v3_distributed_haiku_max_specialist_batch`
  (default 2) whenever the configured specialist model name contains "haiku", clamped to
  never exceed the unrelated `intel_v3_distributed_max_specialist_batch` ceiling; new
  per-task structured log line + 4 session metrics counters (additive to the existing
  JSONB `intel_run_sessions.metrics`, no schema change).
  **Release-blocker follow-up (same PR #484):** the specialist repair loop above was
  correctly bounded at the wrapper (`ask_json`) call-count level, but `LLMClient` itself
  still silently repeated a truncated response against the SAME model/prompt/batch one
  layer down (`_call_with_backoff` at a larger token budget) — invisible to the
  specialist's own ≤3-calls bound and the 1800-token ceiling, since prior tests only
  mocked `ask_json()` and never counted actual `_single_call()` provider requests.
  `ask_json()` gained `retry_truncated_response: bool = True` (legacy default —
  unrelated callers, including the review agent's Sonnet→Haiku fallback, keep the
  internal retry); specialist calls pass `retry_truncated_response=False`, so a
  detected truncation is still recorded in metadata but never silently repeated —
  the specialist's own per-ticker repair owns it instead. Separately, the
  quota/auth-only "skip repair" gate was widened: ANY actual provider-call failure
  (an exhausted rate-limit/transient retry too, not just quota/auth) now skips the
  per-ticker repair loop and returns a retryable task outcome — a parse/truncation
  failure (provider answered, JSON was bad) has no classification and remains
  repair-eligible; a genuine transport failure never gets reinterpreted as
  ticker-level malformed JSON, and an already-validated peer ticker is never
  discarded or re-requested. 4 new tests use a REAL `LLMClient` with only
  `_single_call()` stubbed (never `ask_json()`) to count actual provider requests.
  **Second release-blocker follow-up (same PR #484):** `retry_truncated_response=False`
  closed the hidden truncation retry, but `_call_with_backoff`'s own `max_attempts`
  still defaulted to 4 — a rate-limit/transient failure on one specialist `ask_json()`
  call could still cost up to 4 actual `_single_call()` provider requests internally
  before returning, since the prior rate-limit test only asserted the wrapper-level
  `outcome.llm_calls == 1` and never counted real `_single_call()` invocations.
  `ask_json()` gained `primary_max_attempts: int = 4` (legacy default, threaded into
  the primary `_call_with_backoff(..., max_attempts=primary_max_attempts)` call only —
  the truncation-retry and fallback-model backoff calls are untouched); specialist
  calls now pass `primary_max_attempts=1`, so ANY provider-level failure (quota/auth,
  rate-limit, transient, timeout) costs exactly one actual provider request per
  `ask_json()` call — the durable task's own retry/backoff owns trying again, never
  `LLMClient`'s internal loop. 2 more tests prove this: a legacy caller with the
  default argument still gets up to 4 real backoff attempts, and a quota/auth failure
  makes exactly 1 real `_single_call()` request (in addition to the existing
  rate-limit/provider-failure tests, which now also assert the exact `_single_call()`
  count, not just the wrapper-level `outcome.llm_calls`).
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
