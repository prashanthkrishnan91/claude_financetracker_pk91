# v2 Architecture

## System Overview

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│   Next.js 14    │────▶│   FastAPI         │────▶│   Supabase          │
│   (Vercel)      │     │   (Serverless)    │     │   (PostgreSQL)      │
│                 │     │                   │     │                     │
│  - Tailwind CSS │     │  - Plaid API      │     │  - Users + RLS      │
│  - shadcn/ui    │     │  - Alpaca Markets │     │  - Positions        │
│  - Recharts     │     │  - yfinance       │     │  - Snapshots        │
│  - React Query  │     │  - Rec Engine     │     │  - Price History    │
│  - Supabase JS  │     │  - DRIP Analytics │     │  - Transactions     │
└─────────────────┘     └──────────────────┘     │  - Recommendations  │
                                                  └─────────────────────┘
```

## Authentication Flow

```
User → Supabase Auth (email/password or OAuth)
     → JWT issued
     → Next.js stores in httpOnly cookie
     → FastAPI validates JWT on every request via Supabase
```

## Data Flow

1. **Plaid Sync**: FastAPI → Plaid API → Parse holdings → Write to `positions` table
2. **Price Updates**: FastAPI → Alpaca/Finnhub/CoinGecko → Write to `price_history`
3. **Recommendations**: FastAPI reads positions + prices → Runs rec engine → Writes to `recommendations`
4. **Frontend**: Next.js reads from Supabase (direct via JS client for reads, FastAPI for writes/compute)

## Multi-Agent Pipeline (Phase 5)

```
POST /recommendations/refresh
        │
        ▼ (202 → job_id)
FastAPI BackgroundTasks
        │
        ▼
AgentOrchestrator.run(run_id)
  ├── Phase 1: Bootstrap — load positions + batch prices
  ├── Phase 2: Sentiment Agent (progress 20%)
  │     ├── Finnhub company-news + yfinance news headlines
  │     ├── CoinGecko sentiment_votes_up_percentage (crypto)
  │     └── Claude Sonnet → {sentiment_score, label, summary}
  ├── Phase 3: Technical Agent (progress 45%)
  │     ├── yfinance 1Y OHLCV → SMA20/SMA50 crossover signals
  │     ├── Polygon daily aggs (pct_5d / pct_30d)
  │     └── Claude Sonnet → {signal, score, summary}
  ├── Phase 4: Fundamental Agent (progress 70%)
  │     ├── yfinance: P/E, PEG, EPS, profit margin, debt/equity, beta
  │     ├── CoinGecko: market_cap_rank, pct_24h/7d/30d (crypto)
  │     └── Claude Sonnet → {score, summary}
  ├── Phase 5: Portfolio Manager (progress 85%)
  │     ├── Conviction = Fund×0.50 + Tech×0.30 + Sent×0.20
  │     ├── Concentration penalty (soft 10%, hard 20% of portfolio)
  │     ├── Cash allocation: proportional to conviction × under-weight bonus
  │     └── Batched Claude Sonnet → investment theses + portfolio summary
  └── Phase 6: Persist (progress 100%)
        ├── Write agent_insights rows (one per ticker)
        ├── Expire old recommendations
        └── Insert new recommendations linked to agent_run_id
```

**Polling pattern**: Frontend polls `GET /recommendations/jobs/{id}` every 1.5s.
`AgentProgressTracker` maps `current_agent` string → step chip via regex.
Auto-stops polling when status is `completed` or `failed`.

## API Key Security

- All API keys encrypted with AES-256-GCM at the application layer
- Encryption key stored in Supabase Vault / environment variable (never in DB)
- Keys decrypted only in FastAPI memory during API calls
- RLS ensures users can only access their own encrypted keys

## Database Design Principles

- All monetary values use `NUMERIC(18,6)` — zero float drift
- SHA-256 canonical fingerprints for transaction dedup
- JSONB for flexible snapshot data and allocation breakdowns
- RLS on every user-scoped table
- `updated_at` triggers on mutable tables

## Tables

| Table | Purpose | RLS |
|---|---|---|
| `users` | Profile + encrypted API keys | Own row only |
| `positions` | Current holdings, cost basis, DRIP | Own data |
| `portfolio_snapshots` | Point-in-time captures | Own data |
| `price_history` | Daily OHLCV (shared) | Read: authenticated |
| `transactions` | Full audit trail | Own data |
| `recommendations` | Buy/Sell/Trim/Hold suggestions | Own data |
| `decision_log` | User decisions on recs | Own data |
| `deposit_plans` | Biweekly deployment schedule | Own data |
| `target_allocations` | Target % per ticker | Own data |
| `plaid_sync_log` | Plaid API audit trail | Own data |
| `agent_runs` | Agent pipeline job status + progress | Own data |
| `agent_insights` | Per-ticker thesis, scores, signals | Own data |
