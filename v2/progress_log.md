# v2 Progress Log

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
