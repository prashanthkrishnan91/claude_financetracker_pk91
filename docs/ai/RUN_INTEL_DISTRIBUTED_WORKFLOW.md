# Run Intel — Distributed Workflow (Implementation Contract)

Status: ACTIVE contract for branch `claude/run-intel-distributed-workflow-1qakac`.
Owner: Intel v3. Migration: `v2/database/027_intel_run_distributed_tasks.sql`.

This document is the locked implementation contract for replacing the Run Intel
execution architecture with a durable, distributed task graph. It was written
after a full repository audit (sessions/jobs plumbing, provider/evidence layer,
LLM agents, deterministic policy, snapshot publication, frontend, deployment,
tests) and governs everything implemented on this branch.

---

## 1. Production failure this replaces

Live session `83f28044-f19c-4640-ab2d-14991db4e29d`: 32 ticker jobs created;
first bounded request selected ALK, GOOGL, VHT; result: 3 attempted, 0
succeeded, 3 retryable failures, 0 LLM calls, ~21s elapsed. The frontend then
stopped because a zero-success batch was classified terminal while 29 jobs were
never attempted.

Root causes (verified in code, not assumed):

1. `AgentOrchestrator.run_analyst_refresh_only()`
   (`app/services/agents/orchestrator.py:753`) performs portfolio-wide work
   before any selected ticker analyst: `_fetch_market_bundle_for_user()` (L798),
   `build_portfolio_context()` (L804), `_attach_sec_filing_intelligence()`
   (L809), `_build_and_persist_snapshots()` (L813), `_build_and_persist_features()`
   (L821), `_compute_thesis_scorecards()` (L828). The bounded drain's 20-second
   deadline (`analyst_refresh_on_demand_drain_v1.py:58-60`) expired before the
   LLM stage.
2. The browser drove execution through chained 20-second `POST /intel/v3/run`
   requests (`useRunIntelV3`, `v2/frontend/src/lib/hooks.ts:241-299`).
3. `deriveRunModel` rule 8 (`advisor-readiness.ts:371-380`) classified any
   `failed>0 && succeeded==0` quantum as terminal before rule 9 could see
   `remaining > 0`.

## 2. Current-state execution (retired by this branch)

```
Click → POST /intel/v3/run (≤20s, repeated by browser up to 20×)
  └─ run_intel_session_request (intel_run_session_flow_v1)
       ├─ create session + enqueue analyst_refresh_jobs (per stale ticker)
       ├─ run_on_demand_drain (1 batch × 3 jobs, 20s wall clock)
       │    └─ AnalystRefreshWorker → FullPortfolioAnalystRefreshAdapter
       │         └─ AgentOrchestrator.run_analyst_refresh_only()
       │              [market bundle + context + SEC + snapshots + features
       │               + scorecards ... then per-ticker analysts]
       └─ when all jobs succeeded: run_prewarm_snapshot() → publish
```

## 3. Target execution (this branch)

```
Click → POST /intel/v3/run  (fast: session + frozen scope + task graph only)
  ├─ intel_run_sessions row (workflow_version=2, status=created→running)
  ├─ intel_run_tickers rows (one per active holding — frozen scope)
  ├─ intel_run_tasks seed wave (portfolio_context, macro, per-ticker lane
  │   collectors for the highest-priority tickers; rest created by scheduler)
  └─ ensure_worker_supervisor_running()   ← in-process durable worker

Worker supervisor loop (until no active v2 session):
  scheduler pass (idempotent, dependency waves)
     ├─ collectors fan out per (ticker, lane)   [ticker-scoped only]
     ├─ evidence bundles build per ticker as lanes go terminal
     ├─ specialist batches form (asset-compatible, ≤5 tickers) per axis
     ├─ conditional review tasks on deterministic conflict rules
     ├─ ticker decisions (deterministic; durable evidence writeback)
     └─ portfolio join + certification + ONE session-linked snapshot
  claim tasks (SQL atomic claim w/ lease) → execute → mark terminal

Frontend: POST once → poll GET /intel/v3/sessions/{id}/status (lightweight,
read-only) → reload snapshot on terminal. Page close never stops the run.
```

## 4. Database state machines (migration 027)

### intel_run_sessions (extended)

- New columns: `workflow_version` (1=legacy, 2=distributed), `current_stage`,
  `metrics JSONB`.
- v2 state machine: `created → running → completed | completed_with_gaps |
  failed`. (`publishing` etc. remain legal values for legacy rows;
  `current_stage` carries the v2 sub-stage.)
- `current_stage`: `preparing → collecting_evidence → specialist_analysis →
  deciding → publishing → done`.
- Partial unique index `uq_intel_run_sessions_active_per_user`: at most one
  non-terminal v2 session per user (no accidental overlapping sessions; a new
  click while a session is active returns the active session's status).
- Legacy supersession: migration marks unfinished `workflow_version=1` sessions
  `superseded` (kept for audit; never rewritten successful; snapshots never
  reused). The v2 worker only ever touches `workflow_version >= 2`.

### intel_run_tickers (new)

One row per (session, active holding), frozen at creation:
`asset_type` (equity|etf|crypto from positions.category), `quantity`,
`market_value`, `portfolio_weight_pct`, `cost_basis`, `unrealized_gain_pct`,
`tax_summary`, `prior_action`, `priority`, `required_lanes`,
`state`, `missing_lanes`, `degraded_lanes`, `degradation_reasons`,
`evidence_bundle JSONB` (immutable once built), `decision JSONB`.

State machine: `pending → evidence_ready → analysis_complete → decision_ready →
decided | no_call | failed`. A ticker's `failed` never fails the session by
itself.

### intel_run_tasks (new)

Generic durable queue; scope = ticker XOR batch_key XOR session.
Task types: `collect_portfolio_context`, `collect_macro_context`,
`collect_evidence_lane` (with `lane`), `build_evidence_bundle`,
`specialist_analysis` (with `lane`=axis, `batch_key`), `review_conflict`,
`ticker_decision`, `portfolio_join_publish`.

State machine: `blocked → pending → claimed → succeeded | degraded | failed`
(+ `cancelled`). `pending` requires `next_retry_at <= now()`. Claims are
leases (`lease_expires_at`); expired leases are reclaimable and `attempts`
increments at claim so crash loops still exhaust `max_attempts` (default 3).
Logical idempotency: unique `(run_session_id, task_type, COALESCE(lane,''),
COALESCE(ticker,''), COALESCE(batch_key,''))`.

Claiming: SQL RPC `claim_intel_run_tasks(worker_id, limit, lease_seconds,
run_session_id)` using `FOR UPDATE SKIP LOCKED`; every claim mints a fresh
`claim_token` UUID (the claim-generation fence). Completion via
`complete_intel_run_task` guarded by `claim_owner` + `state='claimed'` +
`claim_token` (a task can never be completed twice, and a stale worker whose
task was reclaimed matches zero rows). The Python store calls the RPC when
available and falls back to the repository-consistent guarded-UPDATE
compare-and-swap (same pattern as `analyst_refresh_job_store_v1.claim_due_jobs`)
when the RPC is missing (pre-migration environments, in-memory test fakes).

Side-effect fencing: every task-owned side effect — specialist output writes,
evidence-bundle/ticker-row transitions (state-CAS guarded), deterministic
decision writes, compatibility evidence writes, publication/session
completion — verifies the CURRENT claim (`owns_claim`: state/owner/token on
the durable row) immediately before writing, and ticker-row transitions
additionally CAS on the expected prior states. A stale leaseholder cannot
overwrite a specialist output, change a ticker decision, attach an old
bundle, alter session completion, or create/activate a competing snapshot
(two-worker tests assert the side-effect rows themselves stay unchanged).

Graph creation is fail-closed and self-repairing: `get_or_create_task`
returns the exact existing task only on a VERIFIED logical-identity conflict
and re-raises every other database error; session creation verifies the exact
expected seed graph (portfolio context + macro + every collector lane for
every frozen ticker) before `created → running`; incomplete shapes stay in
the explicit retryable `created` state and `repair_session_graph` (run by the
supervisor — no browser traffic required) converges every partial-create
shape to exactly one complete graph.

### intel_run_specialist_outputs (new)

One row per (session, ticker, axis) — independently addressable even when the
LLM call was batched. Fields per the mission contract (stance, score,
confidence, key_findings, risks, evidence_refs, missing_evidence, limitations,
valid_until, model, prompt_version, input_fingerprint, batch_key). Unique on
(session, ticker, axis); repair retries upsert, never duplicate. Advisory
research output only — never visible action authority.

### Ownership / cross-user protection

All four operational tables: deny-all RLS (service-role only, migration 018/026
convention) + BEFORE INSERT/UPDATE triggers asserting `NEW.user_id` equals the
owning session's `user_id` (service role bypasses RLS, so the trigger is the
cross-user guard). Status endpoints verify session ownership before returning
anything.

## 5. Task taxonomy and dependency waves

| Task | Scope | Prerequisites | Produces |
|---|---|---|---|
| collect_portfolio_context | session | none | frozen portfolio-level context (cash, allocation, concentration, prior snapshot state) in task output |
| collect_macro_context | session | none | FRED macro artifact (existing `run_fred_macro_evidence`); degraded when no key |
| collect_evidence_lane | ticker+lane | none | normalized lane evidence (existing lane runners / per-ticker fetchers), artifact id in `output_ref` |
| build_evidence_bundle | ticker | all required lanes terminal | immutable bundle on `intel_run_tickers.evidence_bundle` + fingerprint; state → evidence_ready |
| specialist_analysis | batch+axis | bundles of batch tickers ready | one `intel_run_specialist_outputs` row per ticker in batch |
| review_conflict | ticker | conflicting specialist outputs (deterministic trigger) | axis='review' output row |
| ticker_decision | ticker | required axes terminal (or exhausted) | durable evidence writeback (agent_runs/agent_insights/recommendations) + deterministic `decide()` record on ticker row |
| portfolio_join_publish | session | all tickers terminal | ONE session-linked certified snapshot; session terminal state |

The scheduler (`run_scheduler_v1`) is idempotent and safe to run repeatedly: it
computes missing downstream tasks from terminal prerequisites and inserts them
with the logical-unique index absorbing races. It does NOT create every task
upfront — bundle/specialist/decision/publish tasks appear as readiness is
known.

## 6. Evidence-lane taxonomy by asset type

Grounded in providers that exist in the repo today (no new providers, no paid
providers). Lane names reuse the Stage-5G registry namespace.

| Lane | equity | etf | crypto | Provider (existing) | Required? |
|---|---|---|---|---|---|
| price | ✓ | ✓ | ✓ | `data_sources.fetch_price_action` (yfinance), CoinGecko for crypto | required |
| technicals | ✓ | ✓ | — | `data_sources.fetch_price_action` (yfinance 3mo history + indicators) | required (equity/etf) |
| fundamentals | ✓ | — | — | `data_sources.fetch_fundamentals` (yfinance .info) | required (equity) |
| news_sentiment | ✓ | ✓ | — | `data_sources.fetch_yfinance_news` | optional |
| sec_company_facts | ✓ | — | — | `run_sec_companyfacts_evidence` (SEC EDGAR XBRL, flag-gated runner → research_artifacts) | optional |
| sec_catalyst_sentiment | ✓ | — | — | `run_sec_catalyst_sentiment_evidence` (flag-gated runner → research_artifacts) | optional |
| etf_fund_data | — | ✓ | — | `run_etf_nport_holdings_evidence` (SEC NPORT, flag-gated runner → research_artifacts) | optional |
| crypto_market | — | — | ✓ | `fetch_coingecko_market` (price, momentum, rank, sentiment votes, drawdown) | required (crypto) |
| macro (session) | portfolio-scope | | | `run_fred_macro_evidence` (FRED, flag-gated runner → research_artifacts) | optional |

The price/technicals/fundamentals/news lanes call the shared `data_sources`
fetchers directly (same breakers/semaphores as the rest of the app) and
persist their normalized output durably on the task row (`intel_run_tasks.output`),
with TTL reuse against prior succeeded task outputs; the SEC/ETF/macro lanes
go through the existing flag-gated research-worker runners, which own their
`research_artifacts` writes and artifact-level idempotency.

Rules: a collector receives ONE task and touches ONLY that task's ticker (or
the portfolio scope for session tasks). Optional-lane failure → `degraded`
lane recorded on the ticker; required-lane exhaustion → ticker degraded with
explicit `missing_lanes`, decision plane decides reduced confidence / NO CALL.
ETF holdings unavailability never blocks the run.

### Freshness / TTL contract (reuse-first)

Reuses existing repo values; deltas documented here:

- price: refreshed every run (existing `price_latest` SLA 15m).
- technicals: current run, reuse within 24h (registry `technicals: 24h`).
- news_sentiment: 1h TTL (registry).
- fundamentals: 24h yfinance lane TTL (registry); SEC company facts 168h and
  refreshed only when newer filing metadata exists (existing adapter rule).
- etf_fund_data: 2160h/90d (registry).
- macro: once per session (plus registry 24h).
- Collector reuse check: an active `research_artifacts` row for the lane
  younger than the lane TTL short-circuits the fetch (cache hit recorded in
  session metrics).

## 7. Evidence bundle (immutable specialist input)

Built once per ticker after required lanes are terminal; persisted on
`intel_run_tickers.evidence_bundle`:

```json
{
  "run_session_id": "…", "ticker": "AAPL", "asset_type": "equity",
  "as_of": "…", "portfolio_context": {…frozen scope + session context…},
  "market": {…price lane…}, "technical": {…}, "fundamental": {…},
  "valuation": {…}, "sentiment": {…}, "sec": {…}, "catalysts": […],
  "asset_specific": {…etf/crypto…}, "source_refs": […artifact ids…],
  "missing_lanes": […], "degraded_lanes": […], "quality": {…},
  "input_fingerprint": "sha256:…"
}
```

The bundle is the ONLY input specialists see. Specialists never call providers
(`specialist_agents_v1` imports no provider/data_sources module — enforced by
a boundary test).

## 8. Specialist agent contracts

Reuses `LLMClient.ask_json` (`agents/llm.py`) — no new agent framework, no new
models. Axes by asset type:

- equity: `fundamental` (valuation+quality), `technical`, `sentiment`
  (+catalysts), `risk_filing` (only when SEC evidence present).
- etf: `technical`, `sentiment`, `etf_exposure`.
- crypto: `crypto_market` (momentum/volatility/liquidity/drawdown/regime/risk).

Required axes (decision prerequisites): equity {fundamental, technical};
etf {technical, etf_exposure}; crypto {crypto_market}. Sentiment is optional
for equities because its backing news lane is optional — a news-provider
outage must not force NO CALL on an otherwise well-evidenced holding.
Optional axes that fail degrade only themselves.

Batching: scheduler groups evidence_ready tickers by (asset_type, axis) into
batches of ≤ `INTEL_V3_DISTRIBUTED_MAX_SPECIALIST_BATCH` (default 5). One
structured Claude request analyzes the whole batch; output is strict JSON keyed
by ticker. Parallel specialist tasks run under a global LLM semaphore
(`INTEL_V3_DISTRIBUTED_MAX_LLM_CONCURRENCY`, default 2).

Malformed output: one bounded repair retry (re-prompt with the validation
error). A ticker missing/invalid in an otherwise-valid batch response degrades
only that ticker's axis; valid tickers persist. LLM reuse: an existing output
for (user, ticker, axis) with the same `input_fingerprint` and unexpired
`valid_until` is copied into the session instead of a new LLM call.

Review agent (`review_conflict`): created ONLY when deterministic rules fire —
score spread across required axes > 1.0 with confidence ≥ 0.6 on both sides;
or a strong negative axis (score ≤ -0.5) opposing a strong positive axis for a
holding with weight ≥ 5%; or required-axis confidence < 0.3 on a ≥5% holding.
It consumes specialist outputs + cited refs, fetches nothing, and its output is
one more advisory row (axis='review') — it cannot set actions.

## 9. ONE final deterministic decision authority + session-native publication

Final visible Buy/Hold/Trim/Sell authority is EXACTLY ONE call site:
`decision_policy_v1.decide()` inside the `ticker_decision` task. Publication
never runs policy again and never reloads global recommendation state.

Ordering inside `ticker_decision` (single-authority contract):

1. specialist aggregate is built (pure confidence-weighted math);
2. the canonical `DecisionInputV3` is built;
3. `decide()` executes EXACTLY ONCE per decided ticker;
4. the COMPLETE deterministic input and output are persisted on
   `intel_run_tickers.decision` (action, conviction, all bands, blockers,
   suppression, rationale/why texts, plus the full serialized decision
   input — the replay/audit record);
5. compatibility evidence rows (`agent_runs`/`agent_insights`/
   `recommendations`) are written LAST, carrying the FINAL deterministic
   action — they are projections of the decision for legacy surfaces, never
   an independent advisory action that publication reinterprets. No
   BUY/HOLD/TRIM/SELL row exists before canonical policy determined it.

Session-native publication (`session_publication_v1` +
`publication_v1.execute_publication_task`):

- reads ONLY: the exact session row, its frozen `intel_run_tickers` rows,
  their evidence bundles, specialist outputs, persisted deterministic
  decisions and session-level context. The global `ReadOnlyEvidenceAdapter`,
  `run_prewarm_snapshot` and active `recommendations` are NOT read (a test
  fence fails publication on any read of recommendations/agent_insights/
  agent_runs);
- decided cards are rebuilt VERBATIM from the persisted decision record
  (`rebuild_decision_output`) and formatted with the shared pure
  `snapshot_builder.build_snapshot`; the visible card action always equals
  `intel_run_tickers.decision.action`;
- NO CALL / failed tickers never surface any action card (an older session's
  BUY can never appear for them) — they exist ONLY as explicit coverage gaps
  with ticker, state and a plain-English reason;
- the snapshot carries: full frozen holding count, decided/no_call/failed
  counts, explicit gap ticker lists, `run_session_id`, workflow version,
  session status, and specialist/evidence provenance;
- the distributed certification contract (`certify_session_snapshot`)
  verifies before persist: every frozen ticker accounted exactly once, every
  card belongs to THIS session, card action == persisted decision action,
  gap tickers rendered as gaps only, no foreign/duplicate cards, counts
  consistent — otherwise publication fails retryably;
- persistence inserts the ONE session-linked snapshot row (unique index +
  adopt-on-conflict; deactivate-then-insert; cost-guard write flag honored).

Snapshot-source vocabulary (backward compatible):
- `worker_certified` — all frozen tickers decided + certified (green,
  meaning unchanged);
- `worker_certified_with_gaps` — certified over the decided subset with
  every gap explicitly accounted; visibly non-green (frontend renders an
  honest amber completed-with-gaps state, never "fully certified").

Session status truth: `completed` only when ALL frozen tickers decided and
certification passed; `completed_with_gaps` when ≥1 ticker is NO CALL/failed
but every frozen ticker is explicitly accounted; `failed` when publication or
the deterministic join cannot produce a truthful result.

An optional narrator may explain decisions after they are fixed (existing
behavior); no LLM, agent or worker sets the visible action or allocation.

## 10. Retry and partial-failure rules

- Lane collector failure → retries that task only (backoff 30s·2^attempts,
  max 3 attempts); other lanes/tickers proceed.
- Specialist failure → retries that batch/axis task only; persisted outputs
  from other batches/axes remain.
- Ticker exhaustion → ticker `no_call`/`failed` with explicit reasons; the
  other tickers and the session continue.
- Worker crash → lease expiry makes claimed tasks reclaimable; completed work
  is never re-executed (terminal states + fingerprints).
- Publication failure → only `portfolio_join_publish` retries (its own
  attempts budget, default 3); zero collector/specialist re-execution. Exhausted
  publication budget → session `failed` (honest terminal).
- Session terminal rules: `completed` (all tickers decided, published),
  `completed_with_gaps` (published; one or more tickers no_call/failed —
  lane-level degradation is recorded on ticker rows and session metrics
  without reclassifying a fully-decided session), `failed` only for: scope
  cannot be loaded, task graph cannot be created, deterministic policy cannot
  run at all, publication exhausted, or ownership checks fail.

## 11. Deployment model

One in-process worker supervisor (`run_worker_supervisor_v1`) in the existing
Railway `web` service:

- Activated by `POST /intel/v3/run` (`ensure_supervisor_running()`); a
  startup PROBE supervisor also starts on every boot and exits only after a
  SUCCESSFUL zero-active-session query — crash recovery never depends on
  browser traffic and survives a transient boot-time database failure.
- Database outages are never idle: a failed active-session discovery query
  raises a distinct outcome; the supervisor is retained, retries with
  bounded exponential backoff + jitter (1s → 60s, ±30%), performs zero
  provider/LLM work during the outage, and the idle-exit counter advances
  only after a successful query that returned zero active sessions.
- Loop: while an active v2 session exists → scheduler pass → claim (≤N) →
  execute under semaphores → repeat; exits when no active sessions (zero idle
  provider/LLM/database polling afterwards).
- Durable across process termination: state lives in SQL; a restarted process
  resumes from leases/terminal states. NOT FastAPI BackgroundTasks.
- Multiple replicas are safe later via the SKIP LOCKED RPC + leases +
  idempotent task identity; the initial release runs one supervisor.
- The legacy `worker` Railway process (`analyst_refresh_worker_entrypoint`)
  remains ONLY for the legacy background `analyst_refresh_jobs` path (master
  kill switch off in production). It cannot see v2 sessions: the distributed
  flow creates zero `analyst_refresh_jobs` rows, and session-scoped legacy jobs
  cannot be created anymore. One execution authority for Run Intel.

Env vars (all additive):

- `INTEL_V3_DISTRIBUTED_MAX_COLLECTOR_CONCURRENCY` (default 4)
- `INTEL_V3_DISTRIBUTED_MAX_LLM_CONCURRENCY` (default 2)
- `INTEL_V3_DISTRIBUTED_MAX_SPECIALIST_BATCH` (default 5)
- `INTEL_V3_DISTRIBUTED_TASK_LEASE_SECONDS` (default 300)
- `INTEL_V3_DISTRIBUTED_MAX_TASK_ATTEMPTS` (default 3)
- Existing gates unchanged: `INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` (route),
  `INTEL_V3_SNAPSHOT_WRITES_ENABLED` (publication write), `ANTHROPIC_API_KEY`.
- `INTEL_V3_ON_DEMAND_REFRESH_ENABLED` becomes irrelevant to Run Intel
  (documented; retained for nothing new).

### Model cost routing (specialist Haiku, conditional review Sonnet)

`WorkerSupervisor` builds two separate `LLMClient` instances instead of one
shared client — standard specialist analysis never auto-escalates to the
more expensive review model:

- `specialist_llm` (`TASK_SPECIALIST_ANALYSIS`) — `intel_v3_distributed_specialist_model`
  (default `claude-haiku-4-5-20251001`), fallback disabled. A specialist
  failure retries the durable task on the same model; it never falls back to
  Sonnet.
- `review_llm` (`TASK_REVIEW_CONFLICT`) — `intel_v3_distributed_review_model`
  (default `claude-sonnet-5`), falling back once to
  `intel_v3_distributed_review_fallback_model` (default
  `claude-haiku-4-5-20251001`).
- `decision_policy_v1.decide()` remains the only visible Buy/Hold/Trim/Sell
  authority regardless of which model produced the advisory specialist/review
  signal. `TASK_TICKER_DECISION` and publication never touch an LLM client.

Env vars (all additive; existing deployments work unchanged without setting
any of them):

- `INTEL_V3_DISTRIBUTED_SPECIALIST_MODEL` (default `claude-haiku-4-5-20251001`)
- `INTEL_V3_DISTRIBUTED_REVIEW_MODEL` (default `claude-sonnet-5`)
- `INTEL_V3_DISTRIBUTED_REVIEW_FALLBACK_MODEL` (default `claude-haiku-4-5-20251001`)

`LLMClient.fallback_model` is now `Optional[str]`: no fallback is attempted
when it is `None`, empty, or identical to the primary model. Legacy callers
(`services/agents/orchestrator.py` and everything else constructing
`LLMClient()` with defaults) are unaffected — the default fallback stays
Sonnet 4.6 → Haiku 4.5.

## 12. Migration & rollout

1. Merge branch; deploy backend (safe pre-migration: session creation returns
   an explicit retryable error until 027 is applied — same degradation pattern
   as 026).
2. Apply `027_intel_run_distributed_tasks.sql` in Supabase SQL editor
   (idempotent; includes legacy supersession UPDATE).
3. Deploy frontend (poll-based hook).
4. Live validation: one Run Intel click → verify session freezes every active
   ticker, collectors stay ticker-scoped, specialist calls occur after bundle
   readiness, one session-linked snapshot, frontend only polls.

Rollback: the route degrades explicitly if tables are missing; migration
rollback statements are included (commented) in 027.

## 13. Deletion / supersession plan

Retired from Run Intel (deleted on this branch):

- `intel_run_session_flow_v1.py` (bounded-request session flow)
- `analyst_refresh_on_demand_drain_v1.py` (bounded HTTP drain)
- Browser continuation loop in `useRunIntelV3` + terminal-failure precedence
  in `deriveRunModel`
- Session-scoped enqueue into `analyst_refresh_jobs` (`enqueue_session_jobs`)

Retained but boundary-fenced (legacy background path only, flag-off in prod,
clearly deprecated in module docstrings):

- `analyst_refresh_worker_entrypoint` / `analyst_refresh_worker_v1` /
  `full_portfolio_analyst_refresh_adapter_v1` /
  `AgentOrchestrator.run_analyst_refresh_only` — the legacy background worker
  service. Run Intel's route/scheduler/worker cannot import them (enforced by
  static boundary tests).
- `AgentOrchestrator.run()` — unreachable from Run Intel (legacy
  `job_runner` path only, unchanged).

Superseded data: unfinished v1 sessions → `superseded` by migration 027.

## 14. Cost controls

- Global collector concurrency (4), per-provider semaphores (existing
  `data_sources._SEMAPHORES`), global LLM concurrency (2), specialist batch ≤5,
  max attempts 3, lane TTL reuse, fingerprint-based LLM reuse, no LLM call for
  insufficient evidence (bundle gate), zero LLM/provider work on page load or
  status polling, zero idle worker activity (supervisor exits).
- Session `metrics` JSONB: provider calls by lane, cache hits, LLM calls by
  axis, task counts by terminal state, stage durations, token estimates from
  `LLMClient` usage where available. No raw chain of thought stored.

## 15. Acceptance tests (implemented on this branch)

1. Architecture boundary: Run Intel route/worker modules cannot import
   `AgentOrchestrator`, analyst-only orchestrator, drain, or provider-fetching
   agents (static import graph test).
2. Fast session creation: 34-ticker portfolio → 1 session + 34 ticker rows +
   seed tasks, zero provider/LLM/policy/snapshot calls, prompt return.
3. Collector isolation: 3-ticker claim fetches only those tickers; lane
   failure isolation.
4. Cache/freshness: TTL reuse, fingerprint invalidation, no duplicate LLM.
5. Specialist batching: asset-compatible, bounded, bundle-complete, per-ticker
   persistence, malformed-ticker isolation, zero provider calls.
6. Conditional review: aligned → no review; conflict → review; review cannot
   set action.
7. Deterministic authority: actions only from `decide()`; LLM cannot override;
   missing evidence → suppression/NO CALL, not fabricated freshness.
8. Failure isolation (34 holdings): lane/specialist/ticker/worker-crash/
   publication isolation; session reaches completed(_with_gaps).
9. Publication retry: zero collector/LLM calls; exactly one session snapshot.
10. Golden run: deterministic 34-holding fixture (equities+ETFs+crypto) with
    exact provider/LLM call accounting.
11. Frontend: one UUID per click, one create, poll-only observation, unmount
    stops polling not work, session recovery, snapshot reload.
12. SQL contract: migration-027 text contract (atomic claim, lease, unique
    identity, RLS, triggers, retention).
13. Live-regression shape: 32 tickers, ALK/GOOGL/VHT first, ticker-scoped
    collection, failure of those 3 never stops the other 29, no browser
    continuation.

## 16. Non-goals (explicit)

- No redesign of Positions/Paycheck/Deploy Cash/watchlist/auth/hosting.
- No new or paid providers; no Redis/Kafka/Celery/Temporal; no WebSockets.
- No change to the visible recommendation-card contract or `decide()` rules.
- No new agent framework; existing Anthropic client and models only.
- No re-enabling of Watchtower/research/email workers.
- NO CALL is recorded at session/ticker/status/snapshot-metadata level; the
  visible card contract is unchanged in this slice (deterministic policy's
  existing suppression semantics still govern card actions).
