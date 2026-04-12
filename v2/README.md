# Portfolio Intelligence Platform v2

A ground-up rebuild of the Portfolio War Room as a production-grade serverless platform.

**Stack:** FastAPI (Python) + Next.js 14 (TypeScript) + Supabase (PostgreSQL) + Tailwind CSS

> **Branch policy**: All development commits go directly to `main`. No feature branches.

---

## Architecture

```
Next.js 14 (Vercel)  ──▶  FastAPI (Serverless)  ──▶  Supabase (PostgreSQL)
     │                            │
     │ React Query                │ async/await
     │ Supabase JS (auth)         │ Plaid, Alpaca, yfinance
     │ Recharts                   │ AES-256-GCM encryption
     │ Tailwind + shadcn/ui       │ Recommendation engine
     ▼                            ▼
  Mobile-first                API Keys encrypted
  Robinhood aesthetic          at rest in DB
```

See [docs/architecture.md](docs/architecture.md) for detailed system design.

---

## Roadmap

### Phase 1: Database & Architecture Setup
- [x] PostgreSQL schema for Supabase (10 tables, RLS, triggers)
- [x] Pydantic models for all entities (7 model files)
- [x] FastAPI project skeleton (7 routers, 6 services)
- [x] AES-256-GCM encryption service for API keys
- [x] JWT auth middleware (Supabase Auth)
- [x] Next.js 14 project skeleton (Tailwind, shadcn/ui foundations)
- [x] Frontend components: PortfolioSummary, HoldingsList, InsightCard, BottomNav
- [x] v1 migration service (seed 39 positions from bootstrap data)
- [ ] **BLOCKED**: Supabase project creation (needs user account setup)

### Phase 2: High-Performance Financial Engine
- [x] Plaid integration (sync Robinhood holdings) — httpx-based, bypasses plaid-python SDK
- [x] yfinance integration (1-year historical OHLCV)
- [x] Alpaca Markets integration (real-time price updates)
- [x] Async price fetching engine (gather + fallback chain: yfinance → Finnhub → Polygon → Alpaca → CoinGecko)
- [x] Price caching in Supabase price_history table
- [x] CSV import service (SHA-256 dedup from v1)
- [x] PDF statement import (crypto gains tracking)
- [x] Unit + integration tests for all services (70+ test cases)
- [x] Deposit tracking and frequency config
- [x] DRIP analytics (dividend reinvestment tracking)

### Phase 3: The "Robinhood" UI/UX
- [x] Supabase Auth integration (signup/login flow)
- [x] Recharts portfolio performance line chart
- [x] Holdings list with live prices and green/red indicators
- [x] Insight Cards with AI-powered recommendations (Claude API)
- [x] Mobile-responsive bottom nav + tablet sidebar
- [x] React Query integration with all FastAPI endpoints
- [x] Settings page (API key management, deposit config, Plaid/Alpaca/Finnhub sync controls)
- [x] Dashboard: Summary, Holdings, Deposits, DRIP, Insights tabs
- [ ] Deploy to Vercel/Netlify

### Phase 4: Recommendation Engine & Polish
- [x] Claude AI-powered portfolio analysis (senior PM persona)
- [x] Rebalancing recommendations with 6-line narrative
- [x] InsightCards with specific buy/sell/trim actions
- [x] DRIP analytics dashboard (lifetime earnings, annual projections)
- [x] Deposit deployment calculator — formula-mode fallback (no manual targets required)
- [x] Tax-aware recommendations (LT-eligible tracking)
- [x] Deploy tab: built-in allocation formula (NVDA/VOO/VYM/QQQ/ROTATING) with Intel badge enrichment
- [ ] Market trend analysis module
- [ ] End-to-end testing
- [ ] Performance optimization
- [ ] Security audit + CORS hardening

---

## Setup

### Prerequisites

- **Supabase Account**: [Create one here](https://supabase.com/dashboard)
- **Python 3.11+**: For the FastAPI backend
- **Node.js 18+**: For the Next.js frontend

### 1. Supabase Setup

1. Go to [supabase.com/dashboard](https://supabase.com/dashboard) and create a new project
2. In **SQL Editor**, run the migration: `database/001_initial_schema.sql`
3. Copy your project credentials from **Settings → API**:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `SUPABASE_JWT_SECRET` (Settings → API → JWT Settings)

### 2. Backend Setup

```bash
cd v2/backend
cp .env.example .env
# Edit .env with your Supabase credentials
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs available at `http://localhost:8000/docs` (when DEBUG=true).

### 3. Frontend Setup

```bash
cd v2/frontend
cp .env.local.example .env.local
# Edit .env.local with your Supabase URL and anon key
npm install
npm run dev
```

Open `http://localhost:3000` in your browser.

---

## Directory Structure

```
v2/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + lifespan
│   │   ├── config.py            # Settings (env vars)
│   │   ├── database.py          # Supabase client
│   │   ├── models/              # Pydantic models (7 files)
│   │   ├── routers/             # API routes (7 files)
│   │   ├── services/            # Business logic (6 files)
│   │   └── middleware/          # Auth (JWT validation)
│   ├── tests/                   # pytest suite
│   ├── migrations/              # SQL (not used — schema in database/)
│   ├── requirements.txt
│   └── .env.example
│
├── frontend/
│   ├── src/
│   │   ├── app/                 # Next.js 14 app router
│   │   ├── components/          # React components
│   │   ├── lib/                 # Utilities, API client, Supabase
│   │   └── types/               # TypeScript types
│   ├── package.json
│   ├── tailwind.config.ts
│   └── .env.local.example
│
├── database/
│   └── 001_initial_schema.sql   # Full PostgreSQL schema
│
├── docs/
│   └── architecture.md          # System design
│
├── README.md                    # This file
└── progress_log.md              # Detailed change log
```

---

## API Endpoints (Phase 1 Skeleton)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/signup` | Create user account |
| POST | `/api/v1/auth/login` | Login → JWT |
| GET | `/api/v1/auth/me` | Get profile |
| GET | `/api/v1/portfolio/summary` | Dashboard summary |
| GET | `/api/v1/positions/` | List holdings |
| POST | `/api/v1/positions/seed-v1` | Migrate v1 data |
| POST | `/api/v1/prices/batch` | Batch price fetch |
| GET | `/api/v1/prices/{ticker}/history` | OHLCV chart data |
| GET | `/api/v1/recommendations/` | Active InsightCards |
| POST | `/api/v1/recommendations/refresh` | Re-run engine |
| POST | `/api/v1/sync/plaid` | Sync Robinhood |
| POST | `/api/v1/sync/csv/import` | Import CSV |
| GET | `/api/v1/deposits/schedule` | Deposit plan |
| GET | `/health` | Health check |
