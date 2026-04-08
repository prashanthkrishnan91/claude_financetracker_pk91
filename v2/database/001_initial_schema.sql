-- ============================================================================
-- Portfolio Intelligence Platform v2 — Initial Schema
-- Target: Supabase (PostgreSQL 15+)
-- ============================================================================
-- This migration creates all core tables, indexes, RLS policies, and triggers.
-- Run via Supabase SQL Editor or CLI: supabase db push
-- ============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- 1. USERS — Profile & encrypted API keys
-- ============================================================================
-- Supabase Auth handles authentication; this table extends the auth.users
-- profile with app-specific settings and encrypted credentials.
-- API keys are encrypted at the application layer before storage.

CREATE TABLE IF NOT EXISTS public.users (
    id              UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email           TEXT UNIQUE NOT NULL,
    display_name    TEXT,

    -- Encrypted API keys (AES-256-GCM encrypted at app layer, stored as base64)
    encrypted_plaid_access_token    TEXT,
    encrypted_plaid_client_id       TEXT,
    encrypted_plaid_secret          TEXT,
    plaid_env                       TEXT DEFAULT 'sandbox' CHECK (plaid_env IN ('sandbox', 'development', 'production')),

    encrypted_finnhub_api_key       TEXT,
    encrypted_polygon_api_key       TEXT,
    encrypted_alpaca_api_key        TEXT,
    encrypted_alpaca_secret_key     TEXT,

    -- Deposit settings
    deposit_amount      NUMERIC(12,2) DEFAULT 900.00,
    deposit_frequency   TEXT DEFAULT 'biweekly' CHECK (deposit_frequency IN ('weekly', 'biweekly', 'monthly')),

    -- Preferences
    theme               TEXT DEFAULT 'dark' CHECK (theme IN ('dark', 'light')),
    default_currency    TEXT DEFAULT 'USD',

    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE public.users IS 'Extended user profile with encrypted API keys and preferences';
COMMENT ON COLUMN public.users.encrypted_plaid_access_token IS 'AES-256-GCM encrypted Plaid access token (app-layer encryption)';

-- ============================================================================
-- 2. POSITIONS — Current holdings (cost basis, shares, DRIP data)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.positions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('Crypto', 'Core', 'ETF', 'Other', 'IPO', 'SELL')),

    -- Holdings
    shares          NUMERIC(18,6) NOT NULL DEFAULT 0,
    avg_cost        NUMERIC(18,6) NOT NULL DEFAULT 0,

    -- DRIP tracking
    drip_shares     NUMERIC(18,6) DEFAULT 0,
    drip_cost       NUMERIC(18,6) DEFAULT 0,
    divs_received   NUMERIC(18,6) DEFAULT 0,

    -- Analyst targets
    target_price    NUMERIC(18,6),
    bear_price      NUMERIC(18,6),
    bull_price      NUMERIC(18,6),

    -- Tax status
    lt_eligible     BOOLEAN DEFAULT FALSE,
    lt_date         DATE,

    -- Crypto metadata
    coingecko_id    TEXT,

    -- Source tracking
    source          TEXT DEFAULT 'manual' CHECK (source IN ('manual', 'plaid', 'csv_import', 'bootstrap')),
    last_synced_at  TIMESTAMPTZ,

    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(user_id, ticker)
);

CREATE INDEX idx_positions_user_id ON public.positions(user_id);
CREATE INDEX idx_positions_ticker ON public.positions(ticker);
CREATE INDEX idx_positions_category ON public.positions(category);

COMMENT ON TABLE public.positions IS 'Current holdings per user — shares, cost basis, DRIP, tax status';

-- ============================================================================
-- 3. PORTFOLIO_SNAPSHOTS — Point-in-time captures for trend tracking
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.portfolio_snapshots (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Aggregates
    total_equity    NUMERIC(18,2) NOT NULL,
    total_cost      NUMERIC(18,2) NOT NULL,
    total_pnl       NUMERIC(18,2) NOT NULL,
    total_pnl_pct   NUMERIC(8,4),
    cash_balance    NUMERIC(18,2) DEFAULT 0,

    -- Denormalized position data at snapshot time
    positions_data  JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Metadata (data sources, timing, etc.)
    metadata        JSONB DEFAULT '{}'::jsonb,

    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_snapshots_user_id ON public.portfolio_snapshots(user_id);
CREATE INDEX idx_snapshots_user_date ON public.portfolio_snapshots(user_id, snapshot_at DESC);

COMMENT ON TABLE public.portfolio_snapshots IS 'Point-in-time portfolio captures for historical performance tracking';

-- ============================================================================
-- 4. PRICE_HISTORY — Daily OHLCV for charting (1yr+ per ticker)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.price_history (
    id          BIGSERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL,
    price_date  DATE NOT NULL,

    open_price  NUMERIC(18,6),
    high_price  NUMERIC(18,6),
    low_price   NUMERIC(18,6),
    close_price NUMERIC(18,6) NOT NULL,
    volume      BIGINT,

    source      TEXT DEFAULT 'yfinance' CHECK (source IN ('yfinance', 'alpaca', 'polygon', 'finnhub')),
    created_at  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(ticker, price_date)
);

CREATE INDEX idx_price_history_ticker ON public.price_history(ticker);
CREATE INDEX idx_price_history_ticker_date ON public.price_history(ticker, price_date DESC);

COMMENT ON TABLE public.price_history IS 'Daily OHLCV data for charting — populated from yfinance/Alpaca';

-- ============================================================================
-- 5. TRANSACTIONS — Full audit trail (SHA-256 fingerprinted)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.transactions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    fingerprint     TEXT NOT NULL,

    ticker          TEXT,
    tx_type         TEXT NOT NULL CHECK (tx_type IN ('Buy', 'Sell', 'CDIV', 'DRIP', 'SPL', 'ACH', 'RTP', 'Other')),
    quantity        NUMERIC(18,6),
    price           NUMERIC(18,6),
    amount          NUMERIC(18,6),

    tx_date         DATE NOT NULL,
    settle_date     DATE,
    description     TEXT,

    -- Raw CSV/Plaid data preserved for audit
    raw_data        JSONB,

    created_at  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(user_id, fingerprint)
);

CREATE INDEX idx_transactions_user_id ON public.transactions(user_id);
CREATE INDEX idx_transactions_user_ticker ON public.transactions(user_id, ticker);
CREATE INDEX idx_transactions_user_type ON public.transactions(user_id, tx_type);
CREATE INDEX idx_transactions_user_date ON public.transactions(user_id, tx_date DESC);

COMMENT ON TABLE public.transactions IS 'All transactions with SHA-256 canonical fingerprints for dedup';

-- ============================================================================
-- 6. RECOMMENDATIONS — AI/rule-based action suggestions
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.recommendations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL,

    action          TEXT NOT NULL CHECK (action IN ('BUY', 'SELL', 'TRIM', 'HOLD', 'REVIEW')),
    detail          TEXT NOT NULL,
    rationale       TEXT,           -- One-line analyst-style reasoning
    urgency         INTEGER DEFAULT 0 CHECK (urgency BETWEEN 0 AND 4),

    tax_note        TEXT,
    drip_note       TEXT,

    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolution      TEXT CHECK (resolution IN ('accepted', 'rejected', 'deferred', 'expired'))
);

CREATE INDEX idx_recommendations_user_active ON public.recommendations(user_id) WHERE is_active = TRUE;
CREATE INDEX idx_recommendations_user_ticker ON public.recommendations(user_id, ticker);

COMMENT ON TABLE public.recommendations IS 'Buy/Sell/Trim/Hold/Review suggestions with rationale';

-- ============================================================================
-- 7. DECISION_LOG — User actions on recommendations
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.decision_log (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    recommendation_id   UUID REFERENCES public.recommendations(id) ON DELETE SET NULL,
    ticker              TEXT NOT NULL,

    decision            TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected', 'modified', 'deferred')),
    notes               TEXT,

    -- Snapshot of position at decision time
    price_at_decision   NUMERIC(18,6),
    shares_at_decision  NUMERIC(18,6),

    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_decision_log_user ON public.decision_log(user_id);

COMMENT ON TABLE public.decision_log IS 'Audit trail of user decisions on recommendations';

-- ============================================================================
-- 8. DEPOSIT_PLANS — Biweekly/monthly deployment schedule
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.deposit_plans (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    deposit_date    DATE NOT NULL,
    amount          NUMERIC(12,2) NOT NULL,

    -- Allocation breakdown
    allocation      JSONB NOT NULL DEFAULT '{}'::jsonb,    -- {"NVDA": 252.00, "VOO": 198.00, ...}
    rotating_pick   TEXT,

    executed        BOOLEAN DEFAULT FALSE,
    executed_at     TIMESTAMPTZ,

    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_deposit_plans_user ON public.deposit_plans(user_id);
CREATE INDEX idx_deposit_plans_user_date ON public.deposit_plans(user_id, deposit_date);

COMMENT ON TABLE public.deposit_plans IS 'Biweekly deposit schedule with allocation breakdown';

-- ============================================================================
-- 9. TARGET_ALLOCATIONS — User-defined portfolio targets
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.target_allocations (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    target_pct  NUMERIC(8,4) NOT NULL CHECK (target_pct >= 0 AND target_pct <= 100),

    updated_at  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(user_id, ticker)
);

CREATE INDEX idx_target_allocations_user ON public.target_allocations(user_id);

COMMENT ON TABLE public.target_allocations IS 'User-defined target % allocation per ticker';

-- ============================================================================
-- 10. PLAID_SYNC_LOG — Audit trail for Plaid API calls
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.plaid_sync_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    synced_at       TIMESTAMPTZ DEFAULT NOW(),

    account_ids     TEXT[],
    holdings_count  INTEGER,
    cash_balance    NUMERIC(18,2),

    status          TEXT NOT NULL CHECK (status IN ('success', 'error', 'partial')),
    error_message   TEXT,
    duration_ms     INTEGER,

    -- Raw Plaid response (optional, for debugging)
    raw_response    JSONB
);

CREATE INDEX idx_plaid_sync_log_user ON public.plaid_sync_log(user_id);
CREATE INDEX idx_plaid_sync_log_user_date ON public.plaid_sync_log(user_id, synced_at DESC);

COMMENT ON TABLE public.plaid_sync_log IS 'Audit trail for Plaid API sync operations';

-- ============================================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================================
-- Every table with user_id gets RLS: users can only see/modify their own data.

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recommendations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decision_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.deposit_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.target_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.plaid_sync_log ENABLE ROW LEVEL SECURITY;

-- price_history is shared (no user_id) — read-only for authenticated users
ALTER TABLE public.price_history ENABLE ROW LEVEL SECURITY;

-- Users table: users can only read/update their own row
CREATE POLICY users_select ON public.users FOR SELECT USING (auth.uid() = id);
CREATE POLICY users_update ON public.users FOR UPDATE USING (auth.uid() = id);
CREATE POLICY users_insert ON public.users FOR INSERT WITH CHECK (auth.uid() = id);

-- All user-scoped tables: full CRUD on own data
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT unnest(ARRAY[
            'positions', 'portfolio_snapshots', 'transactions',
            'recommendations', 'decision_log', 'deposit_plans',
            'target_allocations', 'plaid_sync_log'
        ])
    LOOP
        EXECUTE format('CREATE POLICY %I_select ON public.%I FOR SELECT USING (auth.uid() = user_id)', tbl, tbl);
        EXECUTE format('CREATE POLICY %I_insert ON public.%I FOR INSERT WITH CHECK (auth.uid() = user_id)', tbl, tbl);
        EXECUTE format('CREATE POLICY %I_update ON public.%I FOR UPDATE USING (auth.uid() = user_id)', tbl, tbl);
        EXECUTE format('CREATE POLICY %I_delete ON public.%I FOR DELETE USING (auth.uid() = user_id)', tbl, tbl);
    END LOOP;
END $$;

-- Price history: any authenticated user can read, only service role can write
CREATE POLICY price_history_select ON public.price_history FOR SELECT
    USING (auth.role() = 'authenticated');

-- ============================================================================
-- TRIGGERS — auto-update updated_at timestamps
-- ============================================================================

CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT unnest(ARRAY['users', 'positions', 'target_allocations'])
    LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON public.%I
             FOR EACH ROW EXECUTE FUNCTION public.update_updated_at()',
            tbl, tbl
        );
    END LOOP;
END $$;

-- ============================================================================
-- SEED DATA FUNCTION — Bootstrap from v1 portfolio.py
-- ============================================================================
-- Call this after user creation to seed their initial positions from v1 data.
-- Usage: SELECT seed_positions_from_v1('user-uuid-here');

CREATE OR REPLACE FUNCTION public.seed_positions_from_v1(p_user_id UUID)
RETURNS INTEGER AS $$
DECLARE
    inserted INTEGER := 0;
BEGIN
    -- This function will be called by the backend after initial signup
    -- to migrate v1 bootstrap data into the user's positions.
    -- The actual data comes from the FastAPI backend (data/portfolio.py).
    RETURN inserted;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION public.seed_positions_from_v1 IS 'Placeholder — backend seeds v1 positions via API after signup';
