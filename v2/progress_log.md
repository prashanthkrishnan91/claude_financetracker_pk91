# v2 Progress Log

## Recent Changes

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

### Repository Reorganization
- Moved all v1 source files (App.py, data_engine.py, price_service.py, etc.) to `v1/` directory
- Created root-level `App.py` shim that patches sys.path and exec's `v1/App.py` — Streamlit Cloud deployment unchanged
- Root `requirements.txt` references `v1/requirements.txt` — Streamlit Cloud reads from root
- Updated `.gitignore` for v1+v2 structure (Python, Node.js, secrets, IDE)
- Created root README explaining repository structure

### Database Schema (Supabase PostgreSQL)
- **10 tables** designed: `users`, `positions`, `portfolio_snapshots`, `price_history`, `transactions`, `recommendations`, `decision_log`, `deposit_plans`, `target_allocations`, `plaid_sync_log`
- **Row Level Security (RLS)** on all user-scoped tables — users can only access their own data
- `price_history` is shared (read-only for authenticated users, write by service role)
- All monetary values use `NUMERIC(18,6)` — zero float drift (carried from v1 principle)
- SHA-256 canonical fingerprints for transaction dedup (carried from v1)
- Auto-updating `updated_at` triggers on mutable tables
- Full SQL in `database/001_initial_schema.sql`

### FastAPI Backend Skeleton
- **7 Pydantic model files**: user, position, portfolio, transaction, recommendation, price, deposit
- **7 API routers**: auth, portfolio, positions, prices, recommendations, sync, deposits
- **6 service files**: portfolio, price, plaid, recommendation_engine, crypto (encryption), migration
- **Config**: pydantic-settings loading from .env
- **Auth**: JWT middleware validating Supabase Auth tokens (HS256)
- **Encryption**: AES-256-GCM for API key storage (encrypt/decrypt round-trip with random nonces)
- **Migration**: `seed_v1_positions()` — transfers all 39 v1 positions to Supabase
- **Tests**: Model validation tests (30+ test cases), encryption round-trip tests

### Next.js 14 Frontend Skeleton
- **App Router** structure with dashboard layout
- **Tailwind CSS** config with Robinhood-inspired dark palette
- **Components**: PortfolioSummaryCard, HoldingsList, PortfolioChart, BottomNav, InsightCard
- **API Client**: Type-safe fetch wrapper matching all backend endpoints
- **React Query** provider configured for data fetching
- **Mobile-first**: Bottom navigation bar (hidden on desktop)
- **Mock data**: Placeholder data in components (replaced by API calls in Phase 3)

### What's Blocked
- **Supabase project**: Need user to create account at supabase.com/dashboard
- **Vercel/Netlify**: Need user to create account for frontend deployment
- **API Keys**: Alpaca, Finnhub, Polygon credentials needed for Phase 2

### Files Created
```
v2/
├── database/001_initial_schema.sql          (265 lines)
├── docs/architecture.md
├── backend/app/main.py
├── backend/app/config.py
├── backend/app/database.py
├── backend/app/models/{user,position,portfolio,transaction,recommendation,price,deposit}.py
├── backend/app/routers/{auth,portfolio,positions,prices,recommendations,sync,deposits}.py
├── backend/app/services/{portfolio,price,plaid,recommendation_engine,crypto,migration}.py
├── backend/app/middleware/auth.py
├── backend/tests/{conftest,test_models,test_crypto_service}.py
├── backend/requirements.txt
├── backend/.env.example
├── frontend/package.json
├── frontend/tsconfig.json
├── frontend/tailwind.config.ts
├── frontend/next.config.js
├── frontend/src/app/{layout,page,providers,globals.css}.tsx
├── frontend/src/app/dashboard/{page,layout}.tsx
├── frontend/src/components/holdings/{PortfolioSummaryCard,HoldingsList}.tsx
├── frontend/src/components/charts/PortfolioChart.tsx
├── frontend/src/components/navigation/BottomNav.tsx
├── frontend/src/components/cards/InsightCard.tsx
├── frontend/src/lib/{utils,api,supabase}.ts
├── frontend/src/types/index.ts
├── frontend/.env.local.example
├── README.md
└── progress_log.md
```
