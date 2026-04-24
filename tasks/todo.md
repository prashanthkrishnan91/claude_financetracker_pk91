# Staged Intelligence Pipeline — Phased Rollout

## Global plan
Evolve the single-LLM orchestrator into a staged pipeline:
1. Data stabilization (MarketSnapshot + failure-safe fallbacks)
2. Deterministic feature engine (no LLM)
3. Per-ticker LLM analyst layer
4. Portfolio synthesis layer
5. Cost + failure controls (FULL / DEGRADED)
6. Frontend UX alignment

Execute one phase at a time. STOP after each phase and report acceptance gates.

---

## Phase 1 — Data Stabilization Layer (current)

### Current-state audit
The existing pipeline already has a lot of the plumbing:
- `services/ai/io_layer.fetch_market_bundle` — parallel, cache-first, breaker-guarded, never raises.
- `services/agents/data_sources.py` — circuit breakers + exponential backoff + 403/429 classification.
- `services/ai/context_builder` — computes a per-ticker `confidence_score` + portfolio-level `completeness_score` from actual fields.
- `services/market_data/system_mode.py` — NORMAL / DEGRADED / LIGHTWEIGHT modes.

**Gaps vs. Phase 1 spec**
1. No explicit `MarketSnapshot` object — state is scattered across the bundle dict + context.
2. No persistence of a per-run, per-ticker snapshot to Supabase.
3. Orchestrator currently fetches prices-only by default (news/fundamentals/price_action only pulled when opted in via explicit args); returns/volatility/sector aren't surfaced on the bundle.
4. The fallback chain (primary → secondary → cached → partial) exists, but is not logged in a uniform, assertable way per run.
5. `data_quality_score` is named `confidence_score` in code; the acceptance spec wants an explicit `data_quality_score` per ticker.
6. `_empty_bundle()` returns `completeness_score: 1.0` — benign (no tickers case), but flagged for review per spec rule.

### Deliverables (Phase 1 only)

1. **New module** `services/intelligence/market_snapshot.py`
   - `MarketSnapshot` dataclass per ticker:
     - `ticker`, `price`, `price_source`
     - `return_1d`, `return_5d`, `return_30d`
     - `volatility_30d` (stddev of daily returns over last ~22 bars)
     - `sector`, `industry`
     - `fundamentals` (compact subset from yfinance)
     - `sentiment_label`, `sentiment_score`, `news_count`, `recent_headlines`
     - `data_quality_score` (0..1, computed from actual fields present)
     - `missing_fields`, `fallback_chain` (list of sources tried in order)
   - `build_market_snapshots(bundle, tickers, prior_insights) -> dict[str, MarketSnapshot]`
     - Pure function — no DB, no network. Consumes the io_layer bundle.

2. **Extend** `services/agents/data_sources.fetch_yfinance_history_sync`
   - Add `return_1d` (last / prev close) and `volatility_30d` (stddev of daily log-returns).
   - Keep existing keys stable — only additive.

3. **Orchestrator integration** (`services/agents/orchestrator.py`)
   - Switch the pre-LLM fetch to `io_layer.fetch_market_bundle(..., include_news=True, include_fundamentals=True, include_price_action=True)`.
   - Build `MarketSnapshot`s from the bundle.
   - Record the fallback chain per ticker in a structured log line: `snapshot_fallbacks ticker=TSLA chain=polygon→yfinance→cache`.

4. **Persistence** — `services/intelligence/snapshot_store.py`
   - Inserts one row per ticker into a new Supabase table `market_snapshots` with columns:
     `run_id, user_id, ticker, as_of, price, return_1d, return_5d, return_30d, volatility_30d, sector, sentiment_label, news_count, data_quality_score, missing_fields (jsonb), fallback_chain (jsonb), raw (jsonb)`.
   - Migration SQL shipped as `v2/backend/migrations/008_market_snapshots.sql` (idempotent).
   - Best-effort insert — if the table is missing the orchestrator logs a warning but does NOT fail the run.

5. **Completeness fix**
   - In `io_layer._empty_bundle()`, leave `completeness_score = 1.0` only when `tickers=[]` (document the exception). Otherwise drive the score strictly from `_compute_completeness`.
   - In `build_market_snapshots`, `data_quality_score` is computed per ticker from weighted actual-fields-present (no hardcoded 1.0).

6. **Tests** — `v2/backend/tests/test_market_snapshot.py`
   - `test_data_quality_score_varies_across_tickers`
   - `test_429_does_not_crash_pipeline` (simulate via monkeypatched data_sources)
   - `test_fallback_chain_logged_when_primary_fails`
   - `test_missing_prices_still_produce_snapshot`

### Acceptance gates (verify before reporting)
- [ ] `pytest v2/backend/tests/test_market_snapshot.py -q` passes.
- [ ] Simulated 429 from Finnhub → pipeline returns successfully; logs show `fallback_chain` containing at least two sources.
- [ ] Every ticker in the portfolio ends up with a `MarketSnapshot` instance (in-memory) and a row in `market_snapshots` (when table present).
- [ ] `data_quality_score` is not constant across a mixed test portfolio.
- [ ] No `completeness = 1.0` hardcode survives outside the `tickers=[]` branch.

### Out of scope (explicit)
- No new LLM calls.
- No UI changes.
- No feature-engine work (Phase 2).
- No changes to `v1/*` or non-recommendation routers.

---

## Phases 2–6
See spec in the original task description.

---

## Phase 2 — Feature Engine Layer (no LLM)

Deterministic, per-ticker feature generation over the persisted `MarketSnapshot`
rows + the io_layer bundle.

### Deliverables
1. `services/intelligence/feature_engine.py`
   - `FeatureSet` dataclass per ticker:
     - `trend_regime` ∈ {uptrend, range, downtrend}
     - `momentum_score` (float, blended 5d + 30d normalized returns)
     - `volatility_regime` ∈ {low, medium, high}
     - `relative_strength_30d` (float, delta vs SPY return_30d)
     - `relative_strength_label` ∈ {outperforming, inline, underperforming}
     - `sector`, `industry`, `category`
     - `data_quality_score` (propagated from MarketSnapshot)
   - `build_features(snapshots, bundle, benchmark) -> dict[str, FeatureSet]`
     - Pure transform. No IO. Uses SMA20/SMA50 from `bundle["price_action"]`
       for trend_regime.
2. `services/intelligence/feature_store.py`
   - Best-effort insert into `agent_features` table.
   - Missing table → single WARN, run continues.
3. `services/intelligence/benchmark.py`
   - Thin async helper: `fetch_benchmark_price_action(cache, coalescer, symbol="SPY")`
   - Cache-backed, coalesced. Failure → neutral `{}` so features still compute
     (relative strength falls back to absolute momentum).
4. `v2/backend/migrations/009_agent_features.sql` — idempotent schema + RLS.
5. Orchestrator wires in feature build + persist AFTER the snapshot stage and
   BEFORE the LLM stage. The LLM stage itself is unchanged in Phase 2.
6. Tests: `tests/test_feature_engine.py`
   - `test_every_snapshot_produces_a_feature_set`
   - `test_trend_regime_derivation` (uptrend / range / downtrend cases)
   - `test_volatility_regime_thresholds` (low / medium / high boundaries)
   - `test_relative_strength_vs_spy`
   - `test_three_distinct_regimes_across_mixed_portfolio`
   - `test_data_quality_score_propagates`
   - `test_missing_benchmark_falls_back_to_absolute`

### Acceptance gates (Phase 2)
- [ ] Every ticker has a FeatureSet and a row in `agent_features` when the
      table is present.
- [ ] At least 3 distinct `trend_regime` values appear across a mixed-test
      portfolio (e.g. uptrend + range + downtrend).
- [ ] Features are not identical across tickers (momentum_score,
      volatility_regime, relative_strength differ).
- [ ] No LLM call is introduced by Phase 2.

### Out of scope
- LLM changes (Phase 3).
- UI changes (Phase 6).
- Feature persistence format changes to Phase 1 `market_snapshots` table.

---

## Phases 3–6
Not started until Phase 2 gates green + user approval.

---

# Adaptive Allocation Engine — Plan (awaiting approval)

Branch: `claude/complete-deploy-ui-QIX4k`

Goal: Upgrade Deploy from a static "spend all cash" allocator into a market-aware
Adaptive Allocation Engine that decides **how much** to deploy now vs. hold back
based on regime, concentration, volatility, and current portfolio risk.

DO NOT touch: compact_v1 reasoning, ticker scoring model, existing Deploy UX
sections (Why Made the Cut / Deployment Risks / What To Do Now / Top Allocation).

## Plumbing audit (verified)

- Allocation engine: `v2/backend/app/services/allocation_engine.py` — pure
  scoring/constraint module producing `AllocationPlan`. No regime awareness.
  Always tries to fully allocate `cash_to_invest`.
- API: `v2/backend/app/routers/allocation.py` GET `/allocation/plan` calls
  `build_allocation_plan(...)` and serializes via `_plan_to_dict()`.
- Next.js proxy: `v2/frontend/src/app/api/deposit-plan/route.ts` reshapes engine
  output into `DepositPlanResult`.
- Deploy UI: `v2/frontend/src/app/dashboard/deposits/page.tsx` (already has Why
  Made the Cut, Deployment Risks, What To Do Now, Top Allocation, Why This Plan).
- SPY data: `services/intelligence/benchmark.fetch_benchmark_price_action()`
  returns `{last, sma20, sma50, pct_5d, pct_30d, volatility_30d, high_3mo, ...}`
  with cache + coalescer + stale-fallback. **Reuse** — no new fetcher.
- Tests: `v2/backend/tests/test_allocation_engine.py` exists; we extend it.

## Task 1 — Market regime detection (NEW)

**File**: `v2/backend/app/services/regime_engine.py` (~150 LoC)

Pure heuristic scorer over the existing SPY benchmark dict. Async wrapper calls
`fetch_benchmark_price_action()`; pure variant tested with synthetic bundles.

Output `RegimeOutput`: `regime_label` (bull|neutral|risk_off), `regime_score`
0..100, `regime_reasons[]`, `data_quality` (high|medium|low), plus the raw
signals used (`spy_pct_5d`, `pct_30d`, `vs_sma50`, `drawdown`, `vol_30d`).

Heuristics: trend (last vs sma20/sma50), recent return (pct_5d, pct_30d),
volatility (volatility_30d), drawdown (last / high_3mo). Score thresholds:
≥65 bull, ≤35 risk_off, else neutral.

Failure isolation: empty bundle → neutral / data_quality=low. Never raises.

## Task 2 — Adaptive deployment percentage (NEW)

**File**: `v2/backend/app/services/adaptive_deployment.py` (~200 LoC)

Pure module. `adapt_allocation_plan(...)` consumes the engine's allocation list,
the regime, holdings, and emits an `AdaptiveDecision` plus per-row
`StagedAllocation`s.

Base deploy %: bull 90, neutral 70, risk_off 50. Modifiers:
- top theme >40% of plan weight: −15
- top theme 30–40%: −5
- top-2 dominance >60%: −10
- ticker post-deploy weight ≥80% of category cap AND share ≥25%: defer (immediate=0,
  reserve=full), reduce plan-level deploy by 5

Guardrails: ≥25% floor unless `wait`; risk_off cap 60%; bull cap 100%.

Per-row staging: bull=full immediate; neutral split 70/30; risk_off split 50/50;
deferred ticker = 0/full. Sum invariant `immediate + reserve == original`.

## Task 3 — Row-level staging (router glue)

In `allocation.py`:
1. After `build_allocation_plan(...)`, call `regime = await detect_market_regime()`
   and `decision = adapt_allocation_plan(...)`.
2. Extend `_plan_to_dict()` to include per-row `immediate_amount`,
   `reserve_amount`, `staging_instruction`, `execution_timing`, top-level
   `regime` and `adaptive` blocks. Keep existing `amount` unchanged so the
   current UI doesn't break.

## Task 4 — UI changes (additive)

`api.ts` + `deposit-plan/route.ts` extend types and pass through new fields.

`deposits/page.tsx`:
- `RecommendedDeploymentCard`: headline "Deploy $X now", subline "Hold $Y reserve",
  badges for regime + deployment_mode.
- Top allocation table: thin sub-row "Now $X · Reserve $Y · {staging_instruction}"
  shown only when `reserve_amount > 0`.
- `What to Do Now`: prepend "Deploy $X now and hold $Y for pullbacks." Mention
  deferred tickers.
- `Why this plan`: append regime + reserve sentence + concentration adjustment
  if applied (max 3 sentences, all from `adaptive.adaptive_reasons`).

Fallback: when backend returns no `adaptive`/`regime`, UI renders exactly as today.

## Task 5 — Explainability

Covered by `adaptive.adaptive_reasons` in Task 2. Three deterministic templates,
joined into ≤3 sentences.

## Task 6 — Testing

NEW: `tests/test_regime_engine.py`, `tests/test_adaptive_deployment.py`.
Cases match success criteria (bull/neutral/risk_off, missing data, high
current weight, same-theme concentration). Re-run `test_allocation_engine.py`
for regression.

Frontend: `npm run typecheck` (or `npm run build`) if feasible.

## Files touched

NEW: `regime_engine.py`, `adaptive_deployment.py`, two new test files.
EDIT: `routers/allocation.py`, `lib/api.ts`, `api/deposit-plan/route.ts`,
`dashboard/deposits/page.tsx`.
NOT TOUCHED: `allocation_engine.py`, compact_v1, ticker scoring, existing UI sections.

**Status: awaiting user approval before executing.**

