# Stage 12A — Allocation & Rebalancing Reality Audit

**Date:** 2026-06-23
**Auditor:** Claude (claude-sonnet-4-6), read-only, no code changes
**Prerequisite:** Stage 11A/11B certified financial truth
  - `financial_truth_baseline.verdict.truth_status = certified`
  - `reconciliation.reconciliation_status = pass`
  - `snapshot_portfolio_value = 53796.87`
  - `position_derived_market_value = 53759.82`
  - `absolute_difference = 37.05` (0.0689%)

---

## 1. Scope and Method

This audit searched the repository for all code related to:

- Target allocation / portfolio weights
- Rebalancing / deployment sizing
- Contribution / paycheck / deposit allocation
- Recommendation generation (Buy/Hold/Trim/Sell policy)
- Sector / group allocation
- Risk profile / advisor readout
- "What should I buy next" logic

Source search covered: `v2/backend/app/services/`, `v2/backend/app/routers/`,
`v2/backend/tests/`, `v2/database/migrations/`, model files.

No code was changed. No runtime calls were made. No LLM was invoked.

---

## 2. Components Discovered

### 2.1 Allocation Engine

**File:** `v2/backend/app/services/allocation_engine.py`
**Tests:** `v2/backend/tests/test_allocation_engine.py`

Pure scoring + constraint module. Converts analyst insights (compact_v1
schema) into ranked dollar deployment decisions. Has no DB or network IO of
its own.

| Property | Value |
|---|---|
| LLM calls | No |
| Provider calls | No (but its callers fetch live prices before invoking it) |
| DB reads | None (pure function; caller supplies holdings) |
| DB writes | None |
| Uses Stage 11 certified truth | **No** — inputs come from caller; no truth gate at this layer |
| Writes to allocation/recommendation tables | No |

**Inputs expected:**
- `cash_to_invest`
- Current holdings (ticker, category, market_value, theme) — sourced live by router
- compact_v1 analyst insights (action, conviction, confidence) — from `recommendations` table
- Optional `target_weights` — from `portfolio_targets` / `target_allocations` table

**Constraints hardcoded:**
- `MAX_SINGLE_STOCK_WEIGHT = 20.0`
- `MAX_ETF_WEIGHT = 35.0`
- `MAX_SPECULATIVE_WEIGHT = 5.0`
- `MAX_SAME_THEME_WEIGHT = 40.0`
- `MIN_CONFIDENCE = 0.65`
- `MIN_TICKER_ALLOCATION = $25`
- `ROUNDING_STEP = $5`

**Classification: `reuse_after_truth_gate`**

The logic is sound. It becomes trustworthy only when its inputs (insights,
market values) come from Stage 11 certified truth and Stage 11 certified Intel
v3 snapshot rather than stale or unreconciled data.

---

### 2.2 Legacy Allocation Router

**File:** `v2/backend/app/routers/allocation.py`
**Endpoint:** `GET /allocation/plan?cash_to_invest=X`

Orchestrates: holdings fetch → live price fetch → recommendation/insight load
→ allocation_engine → regime detection → adaptive_deployment →
deployment_engine.

| Property | Value |
|---|---|
| LLM calls | No |
| Provider calls | **Yes** — fetches live prices from price_engine (Finnhub/Alpaca/Polygon) |
| DB reads | `positions`, `recommendations`, `portfolio_targets`, `agent_runs`, `decision_log` |
| DB writes | None |
| Uses Stage 11 certified truth | **No** — fetches live prices independently; does not check truth certification |
| Uses Intel v3 snapshot | No — reads raw `recommendations` table directly |

**Classification: `reuse_after_truth_gate`**

This route bypasses the Intel v3 certified snapshot path entirely. It loads
raw recommendations (which were considered "untrusted" until Stage 11A
certification) and fetches live prices without the Stage 11 truth gate. The
route must be gated behind a Stage 11 truth check before being used in
production.

---

### 2.3 Recommendation Engine

**File:** `v2/backend/app/services/recommendation_engine.py` (~2765 lines)

Contains both deterministic and LLM paths.

| Method | LLM? | Providers? | Safe? |
|---|---|---|---|
| `generate_rec()` | No | No | Deterministic — safe to reuse after truth gate |
| `build_portfolio_intel()` | No | No | Deterministic snapshot builder — safe after truth gate |
| `compute_portfolio_synthesis()` | No | No | Deterministic — safe after truth gate |
| `portfolio_advisor()` | **Yes (Claude API)** | Optional prices | Must remain labeled advisory-only |
| InsightCard derivation | No | Optional prices | Safe after truth gate |

| Property | Value |
|---|---|
| DB reads | `recommendations`, `agent_insights`, `agent_runs` |
| DB writes | In-memory cache invalidation only (no table writes) |
| Uses Stage 11 certified truth | **No** — no truth gate at this layer |

**Classification:** Deterministic methods → `reuse_after_truth_gate`.
`portfolio_advisor()` (LLM) → `patch_required` — must be clearly labeled
advisory-only and never shown as Buy/Hold/Trim/Sell authority.

---

### 2.4 Intel V3 Service (Sync Read Path)

**File:** `v2/backend/app/services/intelligence/v3/intel_v3_service.py`
**Endpoints:** `GET /intel/v3/snapshot`, `POST /intel/v3/run`

Synchronous read path (`get_latest_snapshot()`) is zero-LLM, zero-provider.
Background worker path calls LLM per ticker but this is deferred by cost
guard.

| Property | Value |
|---|---|
| LLM calls | No (sync read path) / Yes (background worker, currently disabled by cost guard) |
| Provider calls | No (sync path) |
| DB reads | `intel_v3_snapshots` (read), `recommendations`, `agent_insights` |
| DB writes | `intel_v3_snapshots` (write guard active: `INTEL_V3_SNAPSHOT_WRITES_ENABLED=false`) |
| Uses Stage 11 certified truth | **Partial** — snapshot is immutable; re-running after Stage 11 truth certification would produce a snapshot grounded in certified data |

**Classification: `reuse_now`** for the read path. The certified Intel v3
snapshot from a run performed after Stage 11 truth certification is the
correct input for Stage 12B. The background worker must remain disabled per
cost guard until costs are managed.

---

### 2.5 Per-Ticker Analyst (LLM)

**File:** `v2/backend/app/services/intelligence/per_ticker_analyst.py`

LLM analyst producing compact_v1 verdicts: BUY/HOLD/REDUCE/INSUFFICIENT_DATA.

| Property | Value |
|---|---|
| LLM calls | **Yes** — one call per ticker via Anthropic Claude API |
| Provider calls | No |
| DB reads | None (inputs passed in: MarketSnapshot + FeatureSet) |
| DB writes | `agent_insights` (analyst_verdict JSONB, analyst_confidence) |
| Uses Stage 11 certified truth | **No** — inputs are assembled upstream; no truth gate |

**Classification: `reuse_after_truth_gate`**

The compact_v1 schema and strict validation are sound. For verdicts to be
trustworthy, the MarketSnapshot passed in must be sourced from Stage 11
certified positions and prices, not stale or unreconciled data.

---

### 2.6 Deploy V3 Router

**File:** `v2/backend/app/routers/deploy_v3.py`
**Endpoints:** `GET /deploy/v3/plan`, `GET /deploy/v3/readiness`

Reads certified Intel v3 snapshot + sizing bundle. Zero LLM, zero providers.

| Property | Value |
|---|---|
| LLM calls | No |
| Provider calls | No |
| DB reads | `intel_v3_snapshots`, `portfolio_snapshots`, `target_allocations` |
| DB writes | None |
| Uses Stage 11 certified truth | **Partial** — reads `portfolio_snapshots` (which Stage 11B repaired); staleness threshold is 24h |
| Feature flag | `INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` |

**Classification: `reuse_after_truth_gate`**

The architecture is correct: it reads the certified Intel v3 snapshot and uses
`portfolio_snapshots` for sizing. Since Stage 11 has now certified the
portfolio value and price truth, this route is one step away from being
trustworthy — it needs:
1. A re-run of Intel v3 (to rebuild the snapshot using certified data)
2. Certified target allocations (currently missing — see 2.8)

---

### 2.7 Deploy Sizing Source Adapter V1

**File:** `v2/backend/app/services/deploy/deploy_sizing_source_adapter_v1.py`
**Tests:** `v2/backend/tests/test_deploy_sizing_source_adapter_v1.py`

Reads only existing persisted data (zero LLM, zero providers). Builds
`DeploySizingInputBundle` from `portfolio_snapshots` + `target_allocations` +
Settings. Staleness threshold: 24 hours.

| Property | Value |
|---|---|
| LLM calls | No |
| Provider calls | No |
| DB reads | `portfolio_snapshots`, `target_allocations` |
| DB writes | None |
| Uses Stage 11 certified truth | **Yes, partially** — reads `portfolio_snapshots.positions_data[].market_value_usd`; since Stage 11B wrote current prices to `price_history`, a fresh snapshot taken after Stage 11B should have certified market values |
| Trust_status tracking | CERTIFIED / STALE / MISSING / UNSUPPORTED / CONFLICTING per source |

**Classification: `reuse_now`**

The trust tracking and staleness detection are correct. After Stage 11 truth
certification, a fresh `portfolio_snapshots` row (written post-repair) should
give this adapter certified inputs. The `target_allocations` source will
report MISSING until Stage 12B populates it.

---

### 2.8 Deploy Target Allocation Bridge

**File:** `v2/backend/app/services/deploy/deploy_target_allocation_bridge.py`
**Tests:** `v2/backend/tests/test_deploy_policy_allocation_bridge.py`

Pure validation of explicit target allocation inputs. Rejects placeholder
source labels. Requires caller to supply fully-formed allocations.

| Property | Value |
|---|---|
| LLM calls | No |
| Provider calls | No |
| DB reads | None |
| DB writes | None |
| Generates target weights | **No** — explicitly defers this (docstring: "wiring for a canonical portfolio allocation optimizer is deferred to a future stage") |

**Critical gap:** There is no service in the repo that generates target
weights from a portfolio policy. The bridge validates explicit inputs but
produces a `MISSING` trust_status when no allocations are in `target_allocations`.
This is the key missing piece for Stage 12B.

**Classification: `reuse_now`** (the bridge itself is correct). But the
*generator* of target weights does not exist and must be built in Stage 12B.

---

### 2.9 Deployment Engine V2

**File:** `v2/backend/app/services/deployment_engine.py`

Pure deterministic deployment-mode classifier. Produces: deployment_mode,
deploy_now_amount, reserve_amount, per_ticker_allocations.

| Property | Value |
|---|---|
| LLM calls | No |
| Provider calls | No |
| DB reads | None (pure function) |
| DB writes | None |

Scoring 0–100; thresholds: full_deploy ≥70, staged ≥50, defensive ≥30,
skip <30. Per-ticker roles: Primary / Supporting / Watch by conviction tier.

**Classification: `reuse_after_truth_gate`**

The engine itself is correct. It becomes trustworthy when fed certified inputs
from the Intel v3 snapshot + Stage 11 certified market values.

---

### 2.10 Adaptive Deployment

**File:** `v2/backend/app/services/adaptive_deployment.py`

Pure deterministic regime-based deployment percentage calculator.

| Property | Value |
|---|---|
| LLM calls | No |
| Provider calls | No |
| DB reads | None (pure function) |
| DB writes | None |

Base deploy % by regime: bull=100%, neutral=70%, risk_off=50%.

**Classification: `reuse_after_truth_gate`**

Correct logic; needs certified Intel v3 input for regime signal to be
trustworthy.

---

### 2.11 Deposit Service (Paycheck / Biweekly Allocation)

**File:** `v2/backend/app/services/deposit_service.py`
**Table:** `deposit_plans`

Contains hardcoded fixed allocation formula baked into code:

```python
_BREAKDOWN = {
    "NVDA": 0.28,
    "VOO": 0.22,
    "VYM": 0.17,
    "QQQ": 0.17,
    "ROTATING": 0.16,  # rotation: GOOGL, META, AAPL, MSFT, NFLX, CRM, AMD, BRK-B, COST, WMT, XLE, VGT
}
_DEPOSIT_AMOUNT = 900.00
```

| Property | Value |
|---|---|
| LLM calls | No |
| Provider calls | No |
| DB reads | `deposit_plans` |
| DB writes | `deposit_plans` (create + execute) |
| Uses Stage 11 certified truth | **No** — weights are hardcoded constants, not derived from certified portfolio state or Intel v3 signals |
| Connected to Intel v3 | No |

**Classification: `patch_required`**

The hardcoded formula predates Stage 11 and Intel v3. The weights are not
derived from current portfolio weights, certified prices, or Intel v3
conviction signals. The "rotating" list is manually maintained. This is
exactly the "manual target allocation" anti-pattern that Stage 12B must
replace.

The service itself (schedule management, date math, YTD tracking, DB
persistence) is reusable infrastructure. Only the weight-assignment logic
must change.

---

### 2.12 Database Tables Involved

| Table | Relevant to | Safe post-Stage-11? |
|---|---|---|
| `target_allocations` | deploy_sizing_source_adapter, deploy_v3, allocation router | Empty or stale — no certified source yet |
| `portfolio_targets` | allocation_engine (via router) | Likely alias to `target_allocations`; same gap |
| `recommendations` | allocation router, recommendation engine | Was "untrusted" until Stage 11 certification |
| `agent_insights` | recommendation engine, intel_v3_service | LLM-generated; needs fresh run after truth cert |
| `agent_runs` | recommendation engine, allocation router | LLM pipeline state |
| `intel_v3_snapshots` | intel_v3_service, deploy_v3 | Read path safe; content needs re-run after truth cert |
| `analyst_refresh_jobs` | intel_v3_service | Queue for background analyst runs (currently disabled by cost guard) |
| `portfolio_snapshots` | deploy_sizing_source_adapter | Now trustworthy post-Stage-11B price repair |
| `positions` | allocation router, allocation engine | Certified post-Stage-11A/11B |
| `deposit_plans` | deposit_service | Schedule data; allocation formula is untrusted |

---

## 3. What Is and Is Not Safe to Reuse

### Safe to Reuse Now (`reuse_now`)

| Component | Reason |
|---|---|
| `intel_v3_service.py` — read path | Zero LLM, zero providers; returns certified snapshot |
| `deploy_sizing_source_adapter_v1.py` | Trust tracking is correct; `portfolio_snapshots` now certified |
| `deploy_target_allocation_bridge.py` | Pure validation; correct contract; no default weights invented |

### Safe After Truth Gate (`reuse_after_truth_gate`)

| Component | Required gate |
|---|---|
| `allocation_engine.py` | Inputs must come from certified Intel v3 + Stage 11 market values |
| `routers/allocation.py` | Must replace live-price fetch with Stage 11 certified prices; must read Intel v3 snapshot instead of raw recommendations |
| `recommendation_engine.py` — deterministic methods | Fresh LLM run after Stage 11 truth certification needed to re-populate `agent_insights` with trusted inputs |
| `per_ticker_analyst.py` | MarketSnapshot inputs must be derived from Stage 11 certified positions/prices |
| `deploy_v3.py` | Needs certified Intel v3 re-run + Stage 12B target allocations |
| `deployment_engine.py` | Needs certified Intel v3 inputs |
| `adaptive_deployment.py` | Needs certified Intel v3 regime signal |

### Patch Required (`patch_required`)

| Component | What must change |
|---|---|
| `deposit_service.py` — weight formula | Hardcoded NVDA/VOO/VYM/QQQ/ROTATING weights must be replaced with Stage 12B policy-derived targets; rotation list must be driven by Intel v3 BUY signals |
| `recommendation_engine.py` — `portfolio_advisor()` | Must be clearly labeled advisory-only, never shown as Buy/Hold/Trim/Sell authority; currently nothing enforces this at the call site |

### Retire (`retire`)

No components require full retirement. The deposit service schedule/date
infrastructure is reusable. The legacy `/allocation/plan` route may
eventually be superseded by the v3 Deploy path, but should not be removed
until v3 Deploy is fully certified.

### Unknown — Needs Runtime Validation (`unknown_needs_runtime_validation`)

| Component | What is unknown |
|---|---|
| `portfolio_targets` table | Whether this is an alias to `target_allocations` or a separate table; no migration found |
| `target_allocations` table content | Whether any rows exist; `deploy_v3.readiness` endpoint will show `MISSING` if empty |

---

## 4. Critical Gaps

### 4.1 No Target Weight Generator Exists

`deploy_target_allocation_bridge.py` explicitly states it defers "canonical
portfolio allocation optimizer" to a future stage. There is no service that
generates target weights from a portfolio policy. Stage 12B must build this.

**The user must NOT be asked to manually define target weights.** The only
acceptable inputs to the bridge are:
1. Conservative profile policy (built in Stage 12B)
2. Future: user-adjusted overrides on top of the policy baseline

### 4.2 Hardcoded Deposit Formula Is Disconnected from Intel

`deposit_service.py` allocates NVDA 28% / VOO 22% / VYM 17% / QQQ 17% /
ROTATING 16% regardless of current portfolio weights, certified prices, or
Intel v3 BUY signals. A paycheck arriving today would be allocated by a
formula that predates Stage 11 certification.

### 4.3 Legacy Allocation Route Uses Live Prices, Not Stage 11 Truth

`GET /allocation/plan` fetches live prices from Finnhub/Alpaca/Polygon rather
than using Stage 11 certified `price_history`. This means the allocation plan
may use prices that differ from the certified baseline.

### 4.4 Intel V3 Snapshot Needs Re-Run After Stage 11

The existing `intel_v3_snapshots` were generated before Stage 11 price truth
was certified. The content (analyst verdicts, conviction scores) is valid LLM
output, but the market value inputs used at generation time may have been
stale or unreconciled. A fresh `POST /intel/v3/run` after Stage 11
certification would produce a more trustworthy snapshot.

**However:** The cost guard (`INTEL_BACKGROUND_WORKERS_ENABLED=false`) must
remain in place. Re-running Intel requires careful cost management.

---

## 5. Existing Tests — Safety Assessment

| Test file | What it asserts | Unsafe behavior asserted? |
|---|---|---|
| `test_allocation_engine.py` | Pure scoring math, constraints, rounding | No unsafe behavior; tests are correct. Not gated by truth, but this is a pure-function test — acceptable. |
| `test_deploy_policy_allocation_bridge.py` | Certification contract, placeholder rejection | No unsafe behavior. Bridge correctly rejects fabricated weights. |
| `test_deploy_sizing_source_adapter_v1.py` | Trust status derivation, staleness detection | No unsafe behavior. Tests verify MISSING/STALE/CERTIFIED flows correctly. |

No existing tests assert old unsafe behavior that would need to be updated in
this audit PR.

---

## 6. Stage 12B — Recommended Implementation Path

### Preconditions (all satisfied as of Stage 11B)
- `truth_status = certified`
- `reconciliation_status = pass`
- `price_history` populated for all 33 open tickers
- `portfolio_snapshots` has a certified row with per-position `market_value_usd`

### What Stage 12B Must Build

**New service:** `v2/backend/app/services/allocation_policy_v1.py`

Purpose: Generate target weights from a conservative profile policy.
No user input required. No LLM.

Conservative profile policy (starting point, can be tuned):
```
ETF floor:        40% of portfolio must be in ETFs
Max single stock: 20% of portfolio per individual ticker
Max speculative:  5% per speculative-category ticker
Max same theme:   40% per theme group
Target ETF mix:   VOO ~25%, QQQ ~15%, VYM ~10%, remainder flexible
```

The policy reads from:
1. Stage 11 certified `portfolio_snapshots` (current weights per position)
2. `positions` table (ticker, category, theme/group)
3. Current Intel v3 snapshot (BUY / HOLD / TRIM / SELL per ticker)

The policy outputs:
- Per-ticker `target_weight` (0.0–1.0)
- Per-ticker `current_weight` (from certified market values)
- Per-ticker `gap` (target − current, positive = underweight = candidate to buy)
- `source_label = "conservative_profile_policy_v1"`

**New endpoint:** `GET /deploy/v3/next-buy?cash_to_deploy=X`

Purpose: Answer "what should I buy next with $X?"

Algorithm (fully deterministic, zero LLM):
1. Load latest certified Intel v3 snapshot
2. Load Stage 11 certified portfolio weights
3. Run conservative profile policy → target weights + gaps
4. Filter to tickers where:
   - Intel action = BUY
   - gap > 0 (current weight < target weight)
   - gap > minimum threshold (e.g., 1% of portfolio = ~$538 at current value)
5. Rank by: Intel conviction (HIGH first) × normalized gap
6. Size each buy: min(cash × gap_fraction, max_single_trade_amount)
7. Return: ranked buy list with ticker, dollar_amount, reason (gap + conviction)

**Wire into `deploy_target_allocation_bridge.py`:** Replace MISSING trust
status with CERTIFIED when policy-v1 generates weights. Do not invent
weights — if the policy cannot produce a valid target for a ticker (e.g.,
unknown category), leave it as UNSUPPORTED.

**Patch `deposit_service.py`:**
- Replace hardcoded `_BREAKDOWN` dict with policy-derived weights
- Replace manual `_ROTATION_ORDER` list with Intel v3 BUY-ranked tickers
- Keep all schedule/date/YTD tracking logic unchanged

### What Stage 12B Must NOT Do
- No manual target weight entry by the user
- No LLM calls for the numeric allocation output (LLM explanation can follow
  in Stage 12C after numeric output is validated)
- No new SQL migrations unless strictly required
- No provider calls (use Stage 11 certified prices from `price_history`)
- No recommendation generation (read existing Intel v3 snapshot only)
- No changes to Intel v3 Buy/Hold/Trim/Sell decision authority
- No UI changes in Stage 12B (backend + tests only)

### Stage 12B Acceptance Criteria
- `GET /deploy/v3/next-buy?cash_to_deploy=900` returns a deterministic ranked
  list with dollar amounts
- Target weights are generated from policy, not from user input
- All inputs are traceable to Stage 11 certified truth or certified Intel v3 snapshot
- Zero LLM calls in the new endpoint
- Zero provider calls in the new endpoint
- 35+ fixture tests

---

## 7. What This Audit Does Not Do

This audit document does not:
- Change any code
- Run any LLM calls
- Trigger any provider calls
- Write to any database table
- Create any SQL migration
- Change any Buy/Hold/Trim/Sell behavior
- Add any new implementation tests

---

## 8. Component Classification Summary

| Component | File | Classification |
|---|---|---|
| Allocation Engine | `allocation_engine.py` | `reuse_after_truth_gate` |
| Legacy Allocation Router | `routers/allocation.py` | `reuse_after_truth_gate` |
| Recommendation Engine (deterministic) | `recommendation_engine.py` | `reuse_after_truth_gate` |
| Recommendation Engine (portfolio_advisor LLM) | `recommendation_engine.py` | `patch_required` |
| Intel V3 Service (read path) | `intel_v3_service.py` | `reuse_now` |
| Intel V3 Service (background worker) | `intel_v3_service.py` | `reuse_after_truth_gate` (cost guard) |
| Per-Ticker Analyst (LLM) | `per_ticker_analyst.py` | `reuse_after_truth_gate` |
| Deploy V3 Router | `routers/deploy_v3.py` | `reuse_after_truth_gate` |
| Deploy Sizing Source Adapter V1 | `deploy_sizing_source_adapter_v1.py` | `reuse_now` |
| Deploy Target Allocation Bridge | `deploy_target_allocation_bridge.py` | `reuse_now` |
| Deployment Engine V2 | `deployment_engine.py` | `reuse_after_truth_gate` |
| Adaptive Deployment | `adaptive_deployment.py` | `reuse_after_truth_gate` |
| Deposit Service (schedule/date) | `deposit_service.py` | `reuse_now` (infra only) |
| Deposit Service (weight formula) | `deposit_service.py:_BREAKDOWN` | `patch_required` |
| Target allocation generator | (does not exist) | **MISSING — build in Stage 12B** |
