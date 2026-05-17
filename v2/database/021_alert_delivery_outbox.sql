-- ============================================================================
-- Stage 3D — Alert Delivery Outbox v1
-- alert_delivery_outbox — provider-neutral delivery outbox
-- ============================================================================
-- Additive migration — does NOT modify any existing table.
-- Safe to re-run: all statements use IF NOT EXISTS guards.
--
-- WHY: Stage 3D adds a durable delivery outbox between alert candidate
-- generation (Stage 3C) and actual email/push delivery (future stage).
-- Outbox rows are created deterministically from watchtower_alert_candidates
-- rows. No external delivery provider is wired yet.
--
-- MANUAL ACTION REQUIRED (Supabase) — this migration is NOT auto-applied:
--   1. Open the Supabase production project → SQL Editor → New query.
--   2. Paste the contents of this file and Run.
--   3. Verify: SELECT COUNT(*) FROM public.alert_delivery_outbox;  (0 rows is OK)
--   Until applied, the outbox service will return 500 on DB calls.
--   No other app behavior is affected (Intel v3, Deploy, Watchtower,
--   alert candidates, and feedback rows are all unchanged).
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.alert_delivery_outbox (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL,

    -- Source candidate (references watchtower_alert_candidates.id)
    alert_candidate_id    UUID NOT NULL,
    ticker                TEXT NOT NULL,

    -- Delivery classification
    channel               TEXT NOT NULL DEFAULT 'email',
    -- Valid values: email | push | in_app

    delivery_mode         TEXT NOT NULL DEFAULT 'digest',
    -- Valid values: immediate | digest
    -- immediate: SELL + high severity only
    -- digest: default for all other eligible candidates

    severity              TEXT NOT NULL DEFAULT 'normal',
    -- Valid values: low | normal | high

    -- Message content (plain-English only — no raw metrics or fabricated numbers)
    subject               TEXT NOT NULL,
    plain_english_body    TEXT NOT NULL,

    -- Lifecycle
    status                TEXT NOT NULL DEFAULT 'pending',
    -- Valid values: pending | suppressed | sent | failed | cancelled

    -- Idempotency: one row per (user_id, dedupe_key)
    -- dedupe_key = sha256(user_id:alert_candidate_id:channel:policy_version)
    dedupe_key            TEXT NOT NULL,

    -- Provider tracking (nullable until a provider is chosen and delivery occurs)
    provider_message_id   TEXT,
    failure_reason        TEXT,

    -- Timing
    scheduled_for         TIMESTAMPTZ,
    sent_at               TIMESTAMPTZ,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    policy_version        TEXT NOT NULL DEFAULT 'v1',

    CONSTRAINT chk_outbox_channel CHECK (
        channel IN ('email', 'push', 'in_app')
    ),
    CONSTRAINT chk_outbox_delivery_mode CHECK (
        delivery_mode IN ('immediate', 'digest')
    ),
    CONSTRAINT chk_outbox_severity CHECK (
        severity IN ('low', 'normal', 'high')
    ),
    CONSTRAINT chk_outbox_status CHECK (
        status IN ('pending', 'suppressed', 'sent', 'failed', 'cancelled')
    )
);

-- Idempotency: exactly one outbox row per (user_id, dedupe_key).
CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_delivery_outbox_dedupe_key
    ON public.alert_delivery_outbox (user_id, dedupe_key);

-- Per-user chronological lookup (most recent first)
CREATE INDEX IF NOT EXISTS idx_alert_delivery_outbox_user_created
    ON public.alert_delivery_outbox (user_id, created_at DESC);

-- Per-user/status lookup (for delivery worker polling)
CREATE INDEX IF NOT EXISTS idx_alert_delivery_outbox_user_status
    ON public.alert_delivery_outbox (user_id, status, created_at DESC);

-- Per-user/channel lookup
CREATE INDEX IF NOT EXISTS idx_alert_delivery_outbox_user_channel
    ON public.alert_delivery_outbox (user_id, channel, created_at DESC);

-- Per-user/ticker/channel lookup (for noisy-repeat suppression queries)
CREATE INDEX IF NOT EXISTS idx_alert_delivery_outbox_user_ticker_channel
    ON public.alert_delivery_outbox (user_id, ticker, channel, created_at DESC);

-- RLS: authenticated users can read their own rows only.
-- The service role bypasses RLS for internal outbox writes.
ALTER TABLE public.alert_delivery_outbox ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS alert_delivery_outbox_user_policy ON public.alert_delivery_outbox;
CREATE POLICY alert_delivery_outbox_user_policy
    ON public.alert_delivery_outbox FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- ── ROLLBACK (commented out by default) ──────────────────────────────────────
-- DROP TABLE IF EXISTS public.alert_delivery_outbox CASCADE;
