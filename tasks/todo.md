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
