- 2026-05-02: fix(intel-v2): follow-up build fix — renamed AgentInsightCardThesisVisibility test from .ts to .tsx to resolve Vercel JSX parsing error; no logic/UI/backend/SQL/LLM/Deploy changes.
- 2026-05-02: fix(intel-v2): hid AgentInsightCard Business read UI section; WHY/RISK/ACTION/ALT VIEW preserved; thesis_plain_english remains backend-available but not rendered pending differentiated live-output proof; no score math/LLM/Deploy/SQL changes.
- 2026-05-02: fix(intel-v2): end-to-end Business read freshness repair — _build_thesis_fields_for_card now falls back to latest completed run with _thesis_v2 when card's own agent_run_id run is stale/absent; _compute_insight_cards queries latest thesis run and adds to run_lookup; structured logs for latest_thesis_run_id and fallback_used per card; 7 new backend fallback tests + 5 new frontend Business read tests.
- 2026-05-02: Intel v2 backend thesis_plain_english regression fix: `INSUFFICIENT_DATA` summaries now keep conservative headline/caveats but use real per-dimension labels when present, preventing universal identical fallback copy across cards; added focused regression tests for insufficient-with-data and dict/object translation parity.
- 2026-05-02: Intel diagnostic fix (v2/frontend): root cause was live route uses AgentInsightCard (WHY/RISK/ACTION/ALT VIEW) while thesis_plain_english was only rendered in InsightCard; added Business read thesis_plain_english section + focused visibility tests.
- 2026-05-01: Frontend CI/testability hardening (v2/frontend): added explicit ESLint + Jest config and scripts so lint/tests/build run unattended; documented safe placeholder Supabase env usage for local/CI validation.
- 2026-05-01 — Intel v2 PR-4: audited backend quality coverage; added safe deterministic `net_debt_to_ebitda` derivation from existing yfinance `total_debt`/`cash`/`ebitda` payload fields + focused mapper tests; no UI/LLM/Deploy changes.
# v2 Progress Log

> **Archiving policy**: Move entries older than 30 days (or closed milestones) to
> `v2/progress_log_archive.md` to keep this file under ~5 KB.

## Recent Changes

### feat(intel-v2-pr9): render plain-English thesis on Intel cards
- **Date**: May 1, 2026

Intel v2 PR-9 frontend rendering pass:

- Added `ThesisPlainEnglish` interface to `api.ts` and `thesis_plain_english` optional field to `InsightCardData`.
- Added compact `ThesisReadSection` component inside `InsightCard`: "Thesis read" label, headline, label pills, caveats. Omitted silently when field is absent.
- Contract rule enforced: UI binds only to `thesis_plain_english`; `thesis_v2` is never rendered.
- Raw metric keys (`fcf_margin`, `roic_ttm`, `ev_ebitda`, `ps_ttm`, `net_debt_to_ebitda`, etc.) must never appear in rendered copy — covered by contract tests.
- 20 contract tests in `InsightCardThesis.test.ts` covering: present fields, missing field no-crash, thesis_v2 isolation, metric key redaction, and UI-binding correctness.
- No backend changes, no Supabase SQL, no LLM calls, no Deploy changes.

### feat(intel-v2-pr8): expose plain-English thesis response field
- **Date**: May 1, 2026

Intel v2 PR-8 response-wiring pass:

- Added `thesis_plain_english: Optional[dict]` to `InsightCard` (additive, backward-compatible).
- Extended `run_lookup` query in `recommendation_engine._compute_insight_cards` to fetch `allocation` column so per-ticker thesis scorecards can be read from `allocation["_thesis_v2"]`.
- Wired `build_thesis_plain_english()` into card assembly: generates plain-English labels from the scorecard dict; omitted with a debug log on any exception so the response never breaks.
- Both `thesis_v2` (raw scorecard) and `thesis_plain_english` (translated labels) are now populated per card when scorecard data exists.
- 22 new focused tests covering payload presence, thesis_v2 preservation, raw metric key redaction, safe degradation, and no-IO determinism.
- No Supabase SQL, no frontend/UI changes, no Deploy changes, no LLM behavior changes.

### feat(intel-v2-pr6): add safe backend valuation mapping coverage
- **Date**: May 1, 2026

Intel v2 PR-6 backend-only valuation-context pass:

- Added additive yfinance fundamentals payload fields: `ps_ttm` (`priceToSalesTrailing12Months`) and `ev_ebitda` (`enterpriseToEbitda`).
- Added exact deterministic thesis mapper pass-through coverage for `ps_ttm` and `ev_ebitda`.
- Added focused mapper tests covering exact mapping and omission of invalid/missing valuation fields (NaN/None).
- Maintained semantic guardrails: no fake peer medians, no historical baseline fabrication, no PE/PEG-only cheap/expensive labels.
- No Supabase SQL, no frontend/UI changes, no Deploy wiring changes, no LLM behavior changes.

### test(intel-v2-pr3): lock unsafe thesis proxy mappings
- **Date**: May 1, 2026

Intel v2 PR-3 hardening pass for deterministic thesis mapper honesty:

- Added focused mapper tests that assert semantic non-equivalence remains unmapped: `profit_margin→fcf_margin` blocked, `return_on_equity→roic_ttm` blocked, `debt_to_equity→net_debt_to_ebitda` blocked, `earnings_growth→forward_revenue_growth_est` blocked.
- Added explicit mapper note that these thesis inputs are intentionally deferred until true provider/cache fields exist.
- Kept existing safe deterministic mappings unchanged (`pe`, `forward_pe`, `peg`, `revenue_growth`, `beta`, momentum normalization, RS passthrough, SMA signal).
- Added Intel v2 UI guidance note: advanced scoring input names remain backend-only and should be translated to plain-English investor guidance in user-facing surfaces.
- No UI changes, no Deploy changes, no Supabase SQL, no LLM behavior changes.

### feat(intel-v2-pr2): thesis mapper + score_thesis() wiring into recommendation pipeline
- **Date**: May 1, 2026

Intel v2 PR-2 — backend-only, no UI changes, no Supabase SQL, no LLM calls:

- **New module**: `v2/backend/app/services/intelligence/thesis_mapper.py` — pure deterministic mapper `map_to_thesis_inputs(fundamentals, feature_set)`. Maps 10 source-backed fields: trailing_pe, forward_pe, peg, revenue_yoy (from revenue_growth), beta, return_5d/30d (pp→decimal ÷100), relative_strength_vs_spy (pp, no conversion), sma_20_50_signal (derived ±1/0), trend_regime_score (categorical proxy 70/40/20). Omits missing fields; never fakes.
- **Orchestrator wired**: Phase 2.5 `_compute_thesis_scorecards()` runs after feature engine (Phase 2), before LLM (Phase 3). Logs per-ticker status/conviction_band/blended_quality at INFO. ScoreCards serialized into `agent_runs.allocation["_thesis_v2"]` (no schema change — allocation is existing JSONB).
- **InsightCard extended**: nullable `thesis_v2: Optional[dict] = None` field added. Always null until frontend PR.
- **Tests**: 59 focused mapper tests; 99 total (mapper + engine) passing. 12 test scenarios including normalization edge cases, sma signal, missing-field honesty, determinism, no-IO purity, InsightCard compat.
- Architecture invariant upheld: LLM explains results only; numbers remain deterministic.

### feat(intel-v2-pr1): deterministic thesis score engine foundation
- **Date**: May 1, 2026

Intel v2 PR-1 — backend-only, no UI changes, no Supabase SQL, no LLM calls:

- **New module**: `v2/backend/app/services/intelligence/score_schema.py` — pure data models: `ScoreStatus` (READY/PARTIAL/INSUFFICIENT_DATA), `ConvictionBand` (HIGH/MEDIUM/LOW/INSUFFICIENT_DATA), `SubScore` and `ScoreCard` dataclasses with provenance fields (inputs_used, inputs_missing, data_quality, published).
- **New module**: `v2/backend/app/services/intelligence/thesis_engine.py` — deterministic `score_thesis(ticker, inputs) → ScoreCard`. Five subscores: quality (30%), valuation (25%), risk (20%), growth (15%), momentum (10%). Data quality gates enforce honest PARTIAL/INSUFFICIENT_DATA status rather than guessing. All formulas are linear, transparent, and constant-driven. No IO, no LLM, no yfinance.
- **Tests**: 40 focused tests in `test_thesis_engine.py` covering all 10 required scenarios. 40/40 passed.
- Architecture principle: numbers are deterministic; LLM explains and challenges scores in future PRs, never invents them.

### fix(deploy-v2): unify deploy-now denominator across card/table/step3
- **Date**: May 1, 2026

- Fixed Deploy tab consistency bug where top card deploy-now/reserve could diverge from Allocation Breakdown and Step 3.
- Canonicalized frontend selection to v2 fields first: `plan.deploy_now_amount` + `plan.reserve_amount`.
- Removed local Step 2/Step 3 redeployment re-scaling from totals path; row sums and "Deploy now total" now reflect backend `immediate_amount` values directly.
- Added focused wiring tests for explicit staged ($900→$720/$180) and full-deploy ($900→$900/$0) fixtures.

### feat(deploy-v2-pr2): wire deployment_engine into live allocation router path
- **Date**: May 1, 2026

Deploy Logic v2 PR 2 — wiring/integration, no UI redesign, no Supabase SQL:

- **Live wiring**: `classify_deployment()` called in `GET /api/v1/allocation/plan` after allocations are available. v2 decision now determines `deploy_now_amount`, `reserve_amount`, `deployment_mode`, `deployment_confidence`, `reserve_trigger`, and per-ticker `immediate_amount`/`reserve_amount`.
- **Canonical plan amounts**: `plan.recommended_deploy_amount` and `plan.cash_reserve` now mirror v2 values; existing Deploy UI receives v2 decisions transparently.
- **Backward compat**: all pre-existing response fields preserved; `adaptive` block retained for audit/behavior profile.
- **Per-ticker**: `immediate_amount` and `reserve_amount` per row sourced from v2 `per_ticker_allocations`; adaptive `staging_instruction`/`execution_timing` preserved alongside.
- **Frontend passthrough**: `deposit-plan/route.ts` forwards new v2 fields; `api.ts` types extended with `deployment_v2` block and v2 plan fields.
- **Tests**: 16 new focused wiring tests in `test_deployment_wiring.py`; 97 total tests pass (0 regressions).

### feat(deploy-v2-pr1): deterministic deployment-mode classifier, output schema, and backend tests
- **Commit**: `TBD`
- **Date**: May 1, 2026

Deploy Logic v2 PR 1 — backend-only, no UI changes, no Supabase SQL:

- **New module**: `v2/backend/app/services/deployment_engine.py` — deterministic deployment mode classifier replacing the always-some-reserve heuristic.
- **Four deployment modes**: `full_deploy`, `staged_deploy`, `defensive_reserve`, `skip_or_wait` replacing the old `full/partial/defensive/wait` labels.
- **Cash drag penalty**: Bonus added to deployment score when idle reserve has no valid trigger — promotes full deployment by default.
- **Hard reserve trigger rule**: If `reserve_amount > MIN_RESERVE_FOR_TRIGGER ($25)` and no specific non-generic trigger can be generated, engine forces `reserve = 0` and `mode = full_deploy`.
- **Four trigger types**: `technical_pullback` (near-cap tickers), `watch_tier_breakout` (low conviction), `event_driven` (risk-off regime), `concentration_reduction` (theme staging). Each must include specific target tickers and conditions — generic reserve text is blocked.
- **WATCH ticker cap**: Low-conviction tickers capped at 25% of plan allocation.
- **Output schema extended**: `DeploymentDecision` with all required fields; `ReserveTrigger`, `PerTickerDeployment` dataclasses.
- **TypeScript types added**: `DeploymentModeV2`, `DeploymentDecisionV2`, `ReserveTriggerV2`, `PerTickerDeploymentV2`, `TickerRole` in `api.ts` — backward compatible, old `DeploymentMode` unchanged.
- **Tests**: 32 focused backend tests covering all mode paths, hard trigger rule, cash drag, WATCH cap, denominators, no-generic-reserve, data quality confidence, edge cases.
- No allocation logic changes. No LLM calls. No Supabase SQL. Existing `adaptive_deployment.py` and its tests untouched.

### refactor(deploy-step3): correct amount semantics + split Decision History card
- **Commit**: `TBD`
- **Date**: April 30, 2026

Focused Step 3 + Decision History refactor — no allocation logic or LLM changes:

- **Bug fixed**: `buildInitialActualDecisions` now uses deploy-now adjusted amounts per ticker (from `computeAdjustedAmounts`) instead of raw `rec.amount`. Clicking "Use AI Plan" now prefills actual rows totalling the AI deploy-now amount (e.g. $725), not the full deposit ($900).
- **Status semantics fixed**: New `deriveExecutionStatus` helper uses `ai_deploy_now_amount` as denominator. `fully_executed` when actual ≈ deploy-now; `partially_executed` when under; `skipped` when zero; `modified` when tickers replaced/skipped.
- **Copy fixed**: Execution copy now reads e.g. "Executed $725 of $725 planned now. Reserved $175 from your $900 deposit." — no more misleading "$900 of $900 (100%)".
- **UI split**: Step 3 is now **Card A** (AI plan summary, Use AI Plan / Modify / Skip buttons, execution editor, single save button). **Card B** ("Decision History") is a separate card showing past logs with date, status badge, deposit/invested/reserve, ticker actuals, and expandable performance details (7d/30d/90d windows).
- **Idempotency**: save/update path unchanged; rehydration from backend on load unchanged.
- **Tests added**: `decision-log.test.ts` — adjusted-amount sum equals deploy-now not deposit; `deriveExecutionStatus` with deploy-now denominator; $725 actual vs $900 deposit = `fully_executed` not `partially_executed`.
- No Supabase SQL required. No backend changes. No recommendation/allocation changes.

### fix(deploy-step3): stabilize decision-log idempotency, rehydration, and wording
- **Commit**: `TBD`
- **Date**: April 30, 2026

Focused QA pass for Deploy Step 3 persistence and Decision Log performance display:
- Added display-only dedupe helper keyed by `decision_context.session_key` (keeps latest `updated_at`) so Recent Decision Logs prefer current active record without deleting history.
- Updated Deploy Step 3 rehydration/matching to use deduped logs and shared session-key helper, reducing duplicate/noisy log selection after refresh/tab switches.
- Clarified deployment percentages everywhere in Step 3 and Recent Decision Logs (`of deploy-now plan` vs `of total deposit`) to avoid misleading “100% deployed” wording.
- No recommendation/allocation logic changes; no LLM behavior changes; no SQL migration required.

### fix(code-graph): resolve relative and short-name imports correctly
- **Commit**: `9f5bab1`
- **Date**: April 13, 2026

Fixed two import-resolution bugs in `scripts/build_code_graph.py` that caused god-node modules (`database`, `config`, `middleware.auth`) to appear as unresolved ghost nodes with `?` LoC:

1. **Relative imports** (`from ..database import get_db`, level=2) were being appended raw as `"database"` instead of resolved. Added `resolve_relative_import()` implementing Python's importlib `rsplit('.', level-1)` algorithm.
2. **Short-name imports** (FastAPI pattern where `v2/backend/app/` is on sys.path, so `from database import …` works without the full dotted path) now resolved via `build_short_name_map()` leaf-index lookup.

Result: 79 real modules (down from 123 ghost+real), 133 edges, 15 communities, all god nodes show correct LoC and file paths.

**Code review:** [LOW] Return type annotation on `build_short_name_map` says `dict[str, str]` but returns `dict[str, list[str]]` — no runtime impact. [LOW] `os.path.commonprefix` does character-level prefix ranking (not dotted-component). No blocking issues.
**Security review:** No secrets, no injection surfaces. `ast.parse` used (not `exec`). All clear.

**Files**: `scripts/build_code_graph.py` (+105 lines), `graphify-out/GRAPH_REPORT.md` (regenerated), `graphify-out/wiki/index.md` (regenerated)

---

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
