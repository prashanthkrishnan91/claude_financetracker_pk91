-- ============================================================================
-- Migration 007 — Distributed correctness layer
-- ============================================================================
-- Tables + RPCs supporting the v4 distributed correctness lock:
--
--   1. provider_health        — atomic per-provider circuit breaker state.
--      Accompanying RPCs (``increment_failure`` / ``reset_failure`` /
--      ``get_provider_health``) guarantee atomic updates so workers never
--      need read-modify-write cycles.
--
--   2. api_call_ledger        — cross-worker lock ledger for the distributed
--      request coalescer. Rows auto-expire via ``expires_at`` so a crashed
--      dispatcher can't deadlock the system. Waiters poll the row for the
--      dispatcher's result.
--
--   3. system_health          — single-row table tracking the current system
--      mode (NORMAL / DEGRADED / LIGHTWEIGHT). Written by the in-process
--      ``SystemModeManager`` on every mode transition.
-- ============================================================================

-- ── provider_health ─────────────────────────────────────────────────────────
-- Atomic breaker state shared across all Railway workers.
-- Each row represents one upstream provider (finnhub, polygon, coingecko,
-- yfinance, ...). Application code NEVER performs read-modify-write on these
-- rows — instead it calls the RPCs below, which use a single UPDATE with a
-- guard clause to avoid lost updates.

CREATE TABLE IF NOT EXISTS public.provider_health (
    provider     TEXT        PRIMARY KEY,
    failures     INTEGER     NOT NULL DEFAULT 0,
    state        TEXT        NOT NULL DEFAULT 'CLOSED',  -- CLOSED|HALF_OPEN|OPEN
    opened_at    DOUBLE PRECISION,  -- monotonic-equivalent UNIX secs when opened
    last_reason  TEXT,
    last_failure_ts TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (state IN ('CLOSED', 'HALF_OPEN', 'OPEN'))
);

-- ── Atomic failure increment ──────────────────────────────────────────────
-- Single UPDATE … SET failures = failures + 1 — no race. Also escalates to
-- state='OPEN' when we cross the threshold, in a single statement so the
-- check-then-open transition is atomic.
CREATE OR REPLACE FUNCTION public.increment_failure(
    p_provider   TEXT,
    p_reason     TEXT DEFAULT NULL,
    p_threshold  INTEGER DEFAULT 3
) RETURNS public.provider_health
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    result public.provider_health;
BEGIN
    INSERT INTO public.provider_health (provider, failures, state, last_reason, last_failure_ts, updated_at)
    VALUES (p_provider, 1,
            CASE WHEN 1 >= p_threshold THEN 'OPEN' ELSE 'CLOSED' END,
            LEFT(COALESCE(p_reason, ''), 200),
            now(), now())
    ON CONFLICT (provider) DO UPDATE SET
        failures = public.provider_health.failures + 1,
        state = CASE
                    WHEN public.provider_health.failures + 1 >= p_threshold THEN 'OPEN'
                    ELSE public.provider_health.state
                END,
        opened_at = CASE
                        WHEN public.provider_health.failures + 1 >= p_threshold
                             AND public.provider_health.opened_at IS NULL
                            THEN extract(epoch FROM now())
                        ELSE public.provider_health.opened_at
                    END,
        last_reason = LEFT(COALESCE(p_reason, ''), 200),
        last_failure_ts = now(),
        updated_at = now()
    RETURNING * INTO result;

    RETURN result;
END;
$$;

-- ── Atomic success reset ──────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.reset_failure(p_provider TEXT)
RETURNS public.provider_health
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    result public.provider_health;
BEGIN
    INSERT INTO public.provider_health (provider, failures, state, opened_at, last_reason, updated_at)
    VALUES (p_provider, 0, 'CLOSED', NULL, NULL, now())
    ON CONFLICT (provider) DO UPDATE SET
        failures = 0,
        state = 'CLOSED',
        opened_at = NULL,
        last_reason = NULL,
        updated_at = now()
    RETURNING * INTO result;

    RETURN result;
END;
$$;

-- ── Cool-off transition (breaker → HALF_OPEN) ───────────────────────────────
-- Call periodically from the application when reading breaker state —
-- returns the current row after flipping OPEN → HALF_OPEN if the cool-off
-- window has elapsed. Keeps the OPEN window bounded without the application
-- having to manage timers.
CREATE OR REPLACE FUNCTION public.get_provider_health(
    p_provider   TEXT,
    p_cooldown_s DOUBLE PRECISION DEFAULT 90.0
) RETURNS public.provider_health
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    result public.provider_health;
    now_epoch DOUBLE PRECISION := extract(epoch FROM now());
BEGIN
    UPDATE public.provider_health
    SET state = 'HALF_OPEN',
        updated_at = now()
    WHERE provider = p_provider
      AND state = 'OPEN'
      AND opened_at IS NOT NULL
      AND now_epoch - opened_at >= p_cooldown_s
    RETURNING * INTO result;

    IF result IS NULL THEN
        SELECT * INTO result FROM public.provider_health WHERE provider = p_provider;
    END IF;

    RETURN result;
END;
$$;

-- ── api_call_ledger ─────────────────────────────────────────────────────────
-- Lock ledger for the distributed request coalescer. A row per in-flight
-- (provider, endpoint, ticker, params) tuple; rows auto-expire via expires_at
-- so a crashed dispatcher can't deadlock the pipeline.

CREATE TABLE IF NOT EXISTS public.api_call_ledger (
    key         TEXT        PRIMARY KEY,
    owner       TEXT        NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'running',  -- running|done|error
    result      JSONB,
    error       TEXT,
    CHECK (status IN ('running', 'done', 'error'))
);

CREATE INDEX IF NOT EXISTS api_call_ledger_expires_idx
    ON public.api_call_ledger (expires_at);

-- Opportunistic cleanup — call periodically (cron job, or lazily from app
-- code on low-traffic windows) to purge fully-settled rows past their
-- result TTL.
CREATE OR REPLACE FUNCTION public.purge_api_call_ledger(p_ttl_hours INTEGER DEFAULT 24)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM public.api_call_ledger
    WHERE expires_at < now() - (p_ttl_hours || ' hours')::interval;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;

-- ── system_health ──────────────────────────────────────────────────────────
-- Single-row table tracking the operator-visible system mode.
-- The application enforces id=1 on upserts so this table never grows.

CREATE TABLE IF NOT EXISTS public.system_health (
    id               INTEGER     PRIMARY KEY CHECK (id = 1),
    mode             TEXT        NOT NULL DEFAULT 'NORMAL',
    reason           TEXT        NOT NULL DEFAULT 'healthy',
    open_providers   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    provider_status  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (mode IN ('NORMAL', 'DEGRADED', 'LIGHTWEIGHT'))
);

-- Seed the single row so the first upsert from the app doesn't race.
INSERT INTO public.system_health (id, mode, reason)
VALUES (1, 'NORMAL', 'seeded')
ON CONFLICT (id) DO NOTHING;

-- ── RLS ─────────────────────────────────────────────────────────────────────
-- These tables hold operational / observability state, not user data —
-- service-role writes only. RLS stays disabled so the anon/user roles can't
-- read them; all access is through the backend service role.

ALTER TABLE public.provider_health  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.api_call_ledger  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.system_health    ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS by default; no per-row policies needed.
-- Add placeholder deny-all policies so any accidental anon access fails fast.
DROP POLICY IF EXISTS provider_health_service_only ON public.provider_health;
CREATE POLICY provider_health_service_only ON public.provider_health FOR ALL USING (false);

DROP POLICY IF EXISTS api_call_ledger_service_only ON public.api_call_ledger;
CREATE POLICY api_call_ledger_service_only ON public.api_call_ledger FOR ALL USING (false);

DROP POLICY IF EXISTS system_health_service_only ON public.system_health;
CREATE POLICY system_health_service_only ON public.system_health FOR ALL USING (false);
