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

## API Key Security

- All API keys encrypted with AES-256-GCM at the application layer
- Encryption key stored in Supabase Vault / environment variable (never in DB)
- Keys decrypted only in FastAPI memory during API calls
- RLS ensures users can only access their own encrypted keys

## Database Design Principles

- All monetary values use `NUMERIC(18,6)` — zero float drift
- SHA-256 canonical fingerprints for transaction dedup (carried from v1)
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
