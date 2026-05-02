# v2 Progress Log

> **Archiving policy**: Move entries older than 30 days (or closed milestones) to
> `v2/progress_log_archive.md` to keep this file under ~5 KB.

## Recent Changes

### feat(intel): reasoning_v2 builder + dormant persistence (PR 1)
- **Date**: 2026-05-02

Added `v2/backend/app/services/intelligence/reasoning_v2_builder.py` — a pure
deterministic function that fuses analyst verdict data into a structured
`reasoning_v2.0` object. Written dormant into `agent_runs.allocation["_reasoning_v2"]`
per ticker at the end of every Run Agents execution.

- No InsightCard change. No API exposure. No SQL migration. No LLM change.
- No frontend change. No Deploy change. Business read remains hidden.
- Scorecard inputs are always `None` in PR 1 (no thesis_engine yet); builder is forward-compatible.
- See `docs/ai/HANDOFF.md` for inspection SQL and PR 2 guidance.

**Files**: `reasoning_v2_builder.py` (new), `test_reasoning_v2_builder.py` (new),
`orchestrator.py` (wire-up), `docs/ai/HANDOFF.md` (new), `v2/progress_log.md` (updated).

---

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
