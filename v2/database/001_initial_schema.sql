-- ============================================================================
-- Portfolio Intelligence Platform v2 — Initial Schema
-- Target: Supabase (PostgreSQL 15+)
-- ============================================================================
-- Single-user / family model — NO Row Level Security needed.
-- Run via Supabase SQL Editor: paste this entire file and click "Run".
-- ============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- 1. USERS — Profile & encrypted API keys
-- ============================================================================
-- Supabase Auth handles login; this table stores app settings and
-- encrypted credentials. Single-user model: 1-2 rows max.

CREATE TABLE IF NOT EXISTS public.users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT UNIQUE NOT NULL,
    display_name    TEXT,
    role            TEXT DEFAULT 'owner' CHECK (role IN ('owner', 'family')),

    -- Encrypted API keys (AES-256-GCM encrypted at app layer, stored as base64)
    encrypted_plaid_access_token    TEXT,
    encrypted_plaid_client_id       TEXT,
    encrypted_plaid_secret          TEXT,
    plaid_env                       TEXT DEFAULT 'production' CHECK (plaid_env IN ('sandbox', 'development', 'production')),

    encrypted_finnhub_api_key       TEXT,
    encrypted_polygon_api_key       TEXT,
    encrypted_alpaca_api_key        TEXT,
    encrypted_alpaca_secret_key     TEXT,
    encrypted_anthropic_api_key     TEXT,

    -- Deposit settings
    deposit_amount      NUMERIC(12,2) DEFAULT 900.00,
    deposit_frequency   TEXT DEFAULT 'biweekly' CHECK (deposit_frequency IN ('weekly', 'biweekly', 'monthly')),

    -- Preferences
    theme               TEXT DEFAULT 'dark' CHECK (theme IN ('dark', 'light')),
    default_currency    TEXT DEFAULT 'USD',

    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

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
CREATE INDEX idx_positions_category ON public.positions(category);

-- ============================================================================
-- 3. PORTFOLIO_SNAPSHOTS — Point-in-time captures for trend tracking
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.portfolio_snapshots (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    total_equity    NUMERIC(18,2) NOT NULL,
    total_cost      NUMERIC(18,2) NOT NULL,
    total_pnl       NUMERIC(18,2) NOT NULL,
    total_pnl_pct   NUMERIC(8,4),
    cash_balance    NUMERIC(18,2) DEFAULT 0,

    positions_data  JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata        JSONB DEFAULT '{}'::jsonb,

    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_snapshots_user_date ON public.portfolio_snapshots(user_id, snapshot_at DESC);

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

    source      TEXT DEFAULT 'yfinance',
    created_at  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(ticker, price_date)
);

CREATE INDEX idx_price_history_ticker_date ON public.price_history(ticker, price_date DESC);

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
    raw_data        JSONB,

    created_at  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(user_id, fingerprint)
);

CREATE INDEX idx_transactions_user_ticker ON public.transactions(user_id, ticker);
CREATE INDEX idx_transactions_user_date ON public.transactions(user_id, tx_date DESC);

-- ============================================================================
-- 6. RECOMMENDATIONS — Buy/Sell/Trim/Hold suggestions
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.recommendations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL,

    action          TEXT NOT NULL CHECK (action IN ('BUY', 'SELL', 'TRIM', 'HOLD', 'REVIEW')),
    detail          TEXT NOT NULL,
    rationale       TEXT,
    urgency         INTEGER DEFAULT 0 CHECK (urgency BETWEEN 0 AND 4),

    tax_note        TEXT,
    drip_note       TEXT,

    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolution      TEXT CHECK (resolution IN ('accepted', 'rejected', 'deferred', 'expired'))
);

CREATE INDEX idx_recommendations_active ON public.recommendations(user_id) WHERE is_active = TRUE;

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
    price_at_decision   NUMERIC(18,6),
    shares_at_decision  NUMERIC(18,6),

    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 8. DEPOSIT_PLANS — Biweekly deployment schedule
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.deposit_plans (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    deposit_date    DATE NOT NULL,
    amount          NUMERIC(12,2) NOT NULL,

    allocation      JSONB NOT NULL DEFAULT '{}'::jsonb,
    rotating_pick   TEXT,

    executed        BOOLEAN DEFAULT FALSE,
    executed_at     TIMESTAMPTZ,

    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_deposit_plans_user_date ON public.deposit_plans(user_id, deposit_date);

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

    raw_response    JSONB
);

CREATE INDEX idx_plaid_sync_log_user_date ON public.plaid_sync_log(user_id, synced_at DESC);

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

CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON public.users
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

CREATE TRIGGER trg_positions_updated_at BEFORE UPDATE ON public.positions
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

CREATE TRIGGER trg_target_allocations_updated_at BEFORE UPDATE ON public.target_allocations
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

-- ============================================================================
-- GRANT PUBLIC ACCESS (single-user model — no RLS)
-- ============================================================================
-- Since this is a single-user/family app, we grant full access to the
-- authenticated role. The FastAPI backend handles authorization.

GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
