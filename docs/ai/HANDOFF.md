# HANDOFF — Current Repo State

Last updated: 2026-07-18 (Lean-product refactor — see root `REFACTOR_REPORT.md` for full
engine determination, keep/delete rationale, fixes, and test evidence).

**Product architecture (read this first):** The app is a lean personal portfolio tool
with exactly three views:

1. **Positions** (`/dashboard/portfolio`) — holdings with tax lots from
   `GET /positions/tax-lots` (pure `tax_lot_engine.py`, FIFO from transactions): per-lot
   cost basis, unrealized gain, short/long-term status, days-until-long-term countdown.
2. **Recommendations** (`/dashboard/recommendations`) — `GET /recommendations/panel`
   wraps the latest certified Intel v3 snapshot with a one-line rationale
   (`recommendation_rationale_v1.py`: profit threshold, sell-side tax impact at the
   configured bracket, allocation drift, engine reason). Hard rule: no rationale → not
   rendered (enforced backend and frontend).
3. **Watchlist** (`/dashboard/watchlist`) — user-defined tickers + price criteria
   (`watchlist_items` table, migration `v2/database/025_watchlist.sql`), deterministic
   flagging, honest unknown state when prices are missing.

**Decision authority:** the deterministic Intel v3 policy
(`intelligence/v3/decision_policy_v1.decide()`) is the ONLY decision engine in the
codebase. The legacy LLM/agent recommendation surface (recommendation_engine, legacy
`/recommendations` routes, AgentInsightCard path) was removed entirely. The agent
pipeline (`services/agents/*`) remains solely as Intel v3's labeled-advisory evidence
producer via the analyst-refresh adapters.

**Ingestion pipeline (unchanged):** `routers/sync.py` (Plaid sync, Robinhood CSV import,
crypto-PDF import, price refresh) + `import_service` / `plaid_service` / `price_engine`
/ `market_data` / `portfolio_engine` — kept exactly as-is.

**Policy tickers live in config:** every ticker set/map referenced by policy code is in
`v2/backend/app/policy_tickers.json` (override with `POLICY_TICKERS_FILE`), loaded by
`services/policy_tickers.py`, with config-parity tests. Tax settings
(`TAX_RATE_SHORT_TERM`, `TAX_RATE_LONG_TERM`, `LONG_TERM_HOLDING_DAYS`,
`PROFIT_TAKING_THRESHOLD_PCT`) are in `app/config.py`.

**Routers registered:** auth, portfolio, positions (+`/tax-lots`), prices, sync,
intel_v3, recommendations_panel, watchlist. Everything else (diagnostics, deploy_v3,
paycheck advisor, deposits, drip, alerts, decisions/journal, analytics, allocation, ai)
was deleted.

**Workers:** analyst refresh + watchtower entrypoints remain (cost-guard flags
unchanged, all default off). The alert email delivery worker was deleted (Procfile /
railway.toml updated).

**Cost guard posture unchanged:** `INTEL_BACKGROUND_WORKERS_ENABLED=false`,
`INTEL_V3_SNAPSHOT_WRITES_ENABLED=false` by default. Do not re-enable workers casually
(see `docs/deploy/RAILWAY_COST_GUARD.md`).

**Supabase SQL:** `v2/database/025_watchlist.sql` must be applied manually before the
Watchlist view works in production. No other migrations pending.

**Tests:** backend `pytest` 7,404 passing / 0 failing; frontend `jest` 129 passing,
`tsc --noEmit` clean, `next build` green. The previously known ~93 pre-existing failures
(cross-stage `_SupplementalData` dataclass mismatch, event-loop test poisoning, stale
fixtures vs Migration 024 / cost guard / Stage 9F / Stage 13 contracts) are fixed — see
`REFACTOR_REPORT.md`.
