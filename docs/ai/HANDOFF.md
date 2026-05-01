## Last change
Intel v2 PR-7: backend-only deterministic plain-English thesis translation contract (PR: "feat(intel-v2-pr7): add backend-only plain-English thesis translation layer").

## Files touched
- `v2/backend/app/services/intelligence/thesis_plain_english.py` — **new module**. Adds deterministic translator `build_thesis_plain_english(scorecard)` with additive plain-English labels: `headline`, `quality_label`, `valuation_label`, `risk_label`, `momentum_label`, `data_label`, `caveats`.
- `v2/backend/tests/test_thesis_plain_english.py` — **new test file**. Covers COMPLETE positive summary, PARTIAL data-incomplete caveat, INSUFFICIENT_DATA conservative summary, raw metric redaction, and deterministic output.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Contract notes
- Translation is backend-only and additive; no frontend/UI wiring was introduced.
- Raw metric names remain hidden from user-facing copy (`fcf_margin`, `roic_ttm`, `net_debt_to_ebitda`, `ev_ebitda`, `ps_ttm`, peer median labels, interest coverage).
- Future UI should consume plain-English labels, not raw metric keys.

## QA scope completed
- `cd v2/backend && pytest -q tests/test_thesis_plain_english.py tests/test_thesis_mapper.py tests/test_thesis_engine.py` — passed.

## Behavior change
- New backend-only translation contract is available as a module for future API/UI integration.
- No score math changes, no allocation/deploy changes, no LLM behavior changes.

---

## Last change
Intel v2 PR-5: backend-only cash-flow quality coverage via safe fcf_margin derivation (PR: "feat(intel-v2-pr5): add safe fcf_margin derivation from yfinance fundamentals").

## Files touched
- `v2/backend/app/services/agents/data_sources.py` — yfinance fundamentals payload now includes additive backend-only raw fields: `free_cash_flow` (`freeCashflow`), `operating_cash_flow` (`operatingCashflow`), and `revenue` (`totalRevenue`) when available.
- `v2/backend/app/services/intelligence/thesis_mapper.py` — added exact deterministic derivation `fcf_margin = free_cash_flow / revenue` only when both values are numeric and `revenue > 0`; otherwise omitted. No proxy mapping from `profit_margin`.
- `v2/backend/tests/test_thesis_mapper.py` — added focused tests for exact fcf_margin math and omission guardrails (missing numerator/denominator, revenue <= 0, and explicit no-proxy mapping from profit_margin).
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## PR-5 audit findings (cash-flow fields)
- **Found via existing provider path (`yfinance info`)**: `freeCashflow`, `operatingCashflow`, `totalRevenue`.
- **Mapped now (safe/exact)**: `fcf_margin` derived from (`free_cash_flow`, `revenue`) only.
- **Collected but deferred in mapper**: `operating_cash_flow` (no exact current thesis_engine input field for OCF ratio/quality).

## Explicit semantic guardrails upheld
- `profit_margin` is **not** used as `fcf_margin`.
- `return_on_equity` is **not** used as `roic_ttm`.
- `debt_to_equity` is **not** used as `net_debt_to_ebitda`.
- `earnings_growth` is **not** used as `forward_revenue_growth_est`.

## QA scope completed
- `cd v2/backend && pytest -q tests/test_thesis_mapper.py tests/test_thesis_engine.py` — passed.

## Behavior change
- Additive backend-only thesis input coverage: when provider gives free cash flow and total revenue, thesis scoring now receives deterministic `fcf_margin`.
- `operating_cash_flow` is collected in provider payload for future safe wiring but not mapped currently.
- PARTIAL / INSUFFICIENT_DATA behavior remains unchanged when fields are missing.
- No frontend/UI/Deploy/LLM behavior changes.

---

## Last change
Intel v2 PR-4: backend quality coverage audit + safe net-debt mapping (PR: "feat(intel-v2-pr4): add safe net_debt_to_ebitda derivation from existing fundamentals").

## Files touched
- `v2/backend/app/services/agents/data_sources.py` — yfinance fundamentals payload now carries raw backend-only quality components when available: `total_debt`, `cash`, `ebitda` (additive; no existing keys removed).
- `v2/backend/app/services/intelligence/thesis_mapper.py` — added exact deterministic derivation for `net_debt_to_ebitda = (total_debt - cash) / ebitda` only when all components are present and `ebitda > 0`; otherwise omitted. Keeps missing-data honesty and does not proxy-map from `debt_to_equity`.
- `v2/backend/tests/test_thesis_mapper.py` — added focused tests for exact derivation math, invalid/missing omission behavior, and explicit unsafe proxy guardrails.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## PR-4 audit findings (quality field coverage)
- **Found in current provider payload (`yfinance info`)**: `revenue_growth`, `earnings_growth`, `profit_margin`, `debt_to_equity`, `return_on_equity`, plus newly surfaced `total_debt`, `cash`, `ebitda`.
- **Mapped now (safe/exact)**: `net_debt_to_ebitda` derived from (`total_debt`, `cash`, `ebitda`) only.
- **Deferred (not reliably present in current payload contract)**: free cash flow / operating cash flow, total revenue, net income, interest expense / interest coverage inputs, invested capital components, share-count history fields.

## Explicit semantic guardrails upheld
- `profit_margin` is **not** used as `fcf_margin`.
- `return_on_equity` is **not** used as `roic_ttm`.
- `debt_to_equity` is **not** used as `net_debt_to_ebitda`.
- `earnings_growth` is **not** used as `forward_revenue_growth_est`.

## QA scope completed
- `cd v2/backend && pytest -q tests/test_thesis_mapper.py tests/test_thesis_engine.py` — passed.

## Behavior change
- Additive backend-only thesis input coverage: when provider gives debt/cash/EBITDA components, thesis scoring now receives `net_debt_to_ebitda` deterministically.
- PARTIAL / INSUFFICIENT_DATA behavior remains unchanged when fields are missing.
- No frontend/UI/Deploy/LLM behavior changes.

## Remaining provider/cache/schema work
- To cover additional quality metrics safely, later PRs need explicit provider field plumbing and cache contract expansion for cash-flow, income-statement, and share-history data (not proxy substitution).
- Raw metric names remain backend-only intelligence ingredients; user-facing Intel/Deploy continues to require plain-English translations.

---

## Last change
Intel v2 PR-3: mapper hardening for semantic honesty (PR: "test(intel-v2-pr3): lock unsafe thesis proxy mappings").

## Files touched
- `v2/backend/tests/test_thesis_mapper.py` — added focused guardrail tests proving semantically mismatched fundamentals are intentionally omitted: `profit_margin` does not map to `fcf_margin`, `return_on_equity` does not map to `roic_ttm`, `debt_to_equity` does not map to `net_debt_to_ebitda`, and `earnings_growth` does not map to `forward_revenue_growth_est` (or `revenue_yoy`) without an exact source field.
- `v2/backend/app/services/intelligence/thesis_mapper.py` — added explicit deferred-input note documenting that non-equivalent proxy mappings are intentionally blocked for `fcf_margin`, `roic_ttm`, `net_debt_to_ebitda`, `forward_revenue_growth_est`.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## QA scope completed
- `cd v2/backend && pytest -q tests/test_thesis_mapper.py tests/test_thesis_engine.py` — passed.

## Architecture principle enforced
- Mapper honesty is locked: no fake/proxy thesis inputs from non-equivalent fundamentals.
- PARTIAL / INSUFFICIENT_DATA remains expected and valid when provider coverage is incomplete.
- No allocation/deploy/LLM behavior changes.

## Behavior change
- No new runtime mappings added.
- New tests now explicitly fail if unsafe proxy mappings are introduced in future edits.

## Next steps
- Add true provider/cache support (not proxy substitution) for: `fcf_margin`, `roic_ttm`, `net_debt_to_ebitda`, forward revenue estimate inputs, peer medians, history metrics, and insider/guidance/drawdown risk data.
- Intel v2 UI principle: keep advanced scoring ingredient names backend-only. User-facing Intel/Deploy should translate thesis_v2 into plain-English guidance rather than exposing raw metric keys.

---

## Last change
Intel v2 PR-2: deterministic score_thesis() mapper + backend response wiring (PR: "feat(intel-v2-pr2): thesis mapper + score_thesis() wiring into recommendation pipeline").

## Files touched
- `v2/backend/app/services/intelligence/thesis_mapper.py` — **new module**. Pure deterministic mapper: `map_to_thesis_inputs(fundamentals, feature_set) → dict[str, Optional[float]]`. Maps yfinance fundamentals (pe→trailing_pe, forward_pe, peg, revenue_growth→revenue_yoy, beta) and FeatureSet momentum fields (return_5d/30d ÷100 for pp→decimal, relative_strength_30d as-is pp, sma20/sma50→sma_20_50_signal, trend_regime→trend_regime_score proxy). Omits missing fields; never fakes values.
- `v2/backend/app/services/agents/orchestrator.py` — Phase 2.5 added: `_compute_thesis_scorecards(bundle)` method called immediately after Phase 2 (features ready). Logs per-ticker status/conviction_band/blended_quality. Serialized ScoreCards embedded in `agent_runs.allocation` under `_thesis_v2` key (no schema change needed — allocation is JSONB). Module-level `_scorecard_to_dict()` helper added. Imports for `score_thesis`, `map_to_thesis_inputs`, `ScoreCard` added.
- `v2/backend/app/models/recommendation.py` — `InsightCard` gains nullable `thesis_v2: Optional[dict] = None` field. Backward compatible (always None until frontend PR). Existing fields unaffected.
- `v2/backend/tests/test_thesis_mapper.py` — **new test file**. 59 focused tests across 12 scenarios: field mapping, pe→trailing_pe, revenue_growth decimal/pp normalization, return_5d/30d pp→decimal, relative_strength_30d no-conversion, sma signal derivation 1/0/-1, missing fields omitted, honest PARTIAL/INSUFFICIENT_DATA status, determinism, no-IO purity, InsightCard backward compat.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## QA scope completed
- `pytest -q tests/test_thesis_mapper.py tests/test_thesis_engine.py` — 99 passed, 0 failures.
- `pytest -q tests/test_feature_engine.py tests/test_thesis_mapper.py tests/test_thesis_engine.py` — 114 passed, 0 failures.
- Existing recommendation engine tests require `supabase` module (not installed in CI env) — pre-existing limitation; not caused by this PR. The contract test `TestInsightCardBackwardCompat` covers the InsightCard API contract directly.

## Architecture principle enforced
- No DB schema change. ScoreCard stored in existing `agent_runs.allocation` JSONB.
- No LLM calls added. No frontend changes. No new vendors.
- Mapper is pure: no IO, no yfinance calls, no network.
- Missing fields omitted; thesis engine returns honest PARTIAL/INSUFFICIENT_DATA.

## Behavior change
- **New**: Orchestrator Phase 2.5 computes ScoreCards for all tickers after feature engine runs. Logged at INFO level per ticker (status, conviction_band, blended_data_quality).
- **New**: `agent_runs.allocation` JSONB gains `_thesis_v2` key with serialized per-ticker ScoreCards. Accessible via `GET /recommendations/jobs/{id}` as `allocation["_thesis_v2"]`.
- **New**: `InsightCard.thesis_v2` nullable field added to backend schema (always null until frontend PR).
- **No change** to existing allocation amounts, LLM behavior, recommendation logic, or any existing response fields.

## Known issues / next steps
- Intel v2 PR-3 scope: read `_thesis_v2` from `allocation` JSONB when building InsightCards (requires reading agent_runs.allocation in `get_insight_cards()`), populate `InsightCard.thesis_v2`, and design the UI conviction panel.
- Many thesis fields remain missing (roic_ttm, gross_margin, fcf fields, peer medians, etc.) — these require new data source integrations and are out of scope per PR-2.
- `trend_regime_score` is a categorical proxy (uptrend→70, range→40, downtrend→20); not a calibrated momentum score.

## Unit normalization applied
- `return_5d`, `return_30d`: FeatureSet stores percentage-points (e.g., 5.0 = 5 %); divided by 100 → decimal for thesis_engine.
- `revenue_growth` → `revenue_yoy`: yfinance decimal (e.g., 0.12); defensive: if |v| > 5.0 treated as pp and divided by 100.
- `relative_strength_30d` → `relative_strength_vs_spy`: already pp delta — no conversion.
- `pe` → `trailing_pe`, `forward_pe`, `peg`, `beta`: raw multiples/float — no conversion.
- `sma20/sma50` → `sma_20_50_signal`: +1/0/-1 from absolute price levels.

---

## Previous change
Intel v2 PR-1: deterministic thesis score engine foundation (PR: "feat(intel-v2-pr1): deterministic thesis score engine foundation").

## Files touched
- `v2/backend/app/services/intelligence/score_schema.py` — **new module**. Pure data models: `ScoreStatus` enum (READY/PARTIAL/INSUFFICIENT_DATA), `ConvictionBand` enum (HIGH/MEDIUM/LOW/INSUFFICIENT_DATA), `SubScore` dataclass (score 0–100, data_quality 0–1, inputs_used, inputs_missing, published), `ScoreCard` dataclass (ticker, status, 5 subscores, conviction_score, conviction_band, blended_data_quality, inputs_used, inputs_missing, score_version).
- `v2/backend/app/services/intelligence/thesis_engine.py` — **new module**. Deterministic scoring engine: `score_thesis(ticker, inputs) → ScoreCard`. Five subscores (quality, valuation, growth, risk, momentum). Blend weights: quality 0.30 / valuation 0.25 / risk 0.20 / growth 0.15 / momentum 0.10. Data quality gates: subscore not published if data_quality < 0.40; conviction not published if blended quality < 0.50; INSUFFICIENT_DATA when ≥2 major subscores have data_quality < 0.50. All normalizers are linear, clamped to [0, 1]; scores clamped to [0, 100]. No IO, no LLM, no yfinance, no DB.
- `v2/backend/tests/test_thesis_engine.py` — **new test file**. 40 focused tests across 10 scenarios: READY with full data, PARTIAL with missing fields, INSUFFICIENT_DATA on empty inputs, exact conviction blend weights, valuation direction (cheaper = higher score), risk direction (safer = higher score), optional gaap_nongaap_gap, bounds clamping, determinism, momentum precomputed-only.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## QA scope completed
- `pytest -q v2/backend/tests/test_thesis_engine.py` — 40 passed, 0 failures.
- No existing tests run (this PR adds no wiring; regression scope is zero).

## Architecture principle enforced
- Numbers are deterministic. LLM must not invent metrics, scores, or allocation amounts.
- Engine accepts already-collected numeric inputs; LLM layer (future PRs) explains results only.
- Deploy v2 continues to own all allocation math.

## Behavior change
- None in production. New modules are not wired to any router, API, or frontend path.
- No Supabase SQL required. No LLM calls added. No API contracts changed.

## Known issues / next steps
- Intel v2 PR-2 scope: wire `score_thesis()` into per-ticker data collection and expose scores via an Intel API endpoint or existing recommendation pipeline.
- Subscore normalizer ranges are calibrated for growth-equity universe; may need tuning for value/dividend/crypto tickers.
- `peer_ps_median`, `peer_ev_ebitda_median`, `own_5y_ps_median` contribute to scoring only when paired with the primary metric (ps_ttm / ev_ebitda); they still count toward data_quality if present alone.

---

## Previous change
Fix Deploy Logic v2 deploy-now denominator mismatch (PR: "fix(deploy-v2): unify deploy-now denominator across card/table/step3").

## Files touched
- `v2/frontend/src/app/dashboard/deposits/page.tsx` — deploy-now/reserve selection now uses canonical v2 fields (`plan.deploy_now_amount`/`plan.reserve_amount`) before adaptive/legacy fallbacks; Allocation Breakdown uses canonical per-row `immediate_amount` (no local recapping) so row sum and "Deploy now total" stay aligned; Step 3 `deploy_now_amount`, `reserve_amount`, and "Use AI Plan" prefill now use the same canonical denominator.
- `v2/backend/tests/test_deployment_wiring.py` — added explicit $900 staged fixture test (`deploy_now=720`, `reserve=180`, rows sum to 720) and explicit full-deploy fixture test (`deploy_now=900`, `reserve=0`, rows sum to 900).
- `v2/progress_log.md` — concise entry added.

## QA scope completed
- `pytest -q v2/backend/tests/test_deployment_engine.py v2/backend/tests/test_deployment_wiring.py` — passed.

## Root cause
- Frontend used mixed sources for deploy-now semantics: top card/Step 3 preferred adaptive values while Allocation Breakdown totals/rows were transformed again by `computeAdjustedAmounts` (local Watch-cap redistribution), which could diverge from backend `immediate_amount` values and from `deploy_now_amount`.
- Result: contradictory totals (e.g., card shows deploy-now 720/reserve 180 while rows could still total 900).

## Canonical rule
- Canonical deploy-now denominator for Deploy Logic v2 is `plan.deploy_now_amount` (fallback to `plan.recommended_deploy_amount` only for backward compatibility).
- Canonical reserve is `plan.reserve_amount` (fallback to `plan.cash_reserve`).
- Allocation row immediate amounts must use backend row `immediate_amount` directly; no secondary frontend redistribution is allowed in Step 2/Step 3 paths.

# AI Handoff — Investing App

## Last change
Intel v2 PR-7: backend-only deterministic plain-English thesis translation contract (PR: "feat(intel-v2-pr7): add backend-only plain-English thesis translation layer").

## Files touched
- `v2/backend/app/services/intelligence/thesis_plain_english.py` — **new module**. Adds deterministic translator `build_thesis_plain_english(scorecard)` that converts thesis_v2 scorecard status/subscores into additive plain-English labels (`headline`, `quality_label`, `valuation_label`, `risk_label`, `momentum_label`, `data_label`, `caveats`). Supports both `ScoreCard` objects and serialized dict scorecards.
- `v2/backend/tests/test_thesis_plain_english.py` — **new test file**. Covers COMPLETE positive summary, PARTIAL data-incomplete caveat, INSUFFICIENT_DATA conservative summary, raw metric key redaction, and deterministic output.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Contract notes
- Translation is backend-only and additive; no frontend/UI wiring was introduced.
- Raw metric names remain hidden from user-facing copy (no `fcf_margin`, `roic_ttm`, `net_debt_to_ebitda`, `ev_ebitda`, `ps_ttm`, peer median labels, or interest coverage in translation text).
- Future UI should consume plain-English translation labels, not raw metric keys.

## QA scope completed
- `cd v2/backend && pytest -q tests/test_thesis_plain_english.py tests/test_thesis_mapper.py tests/test_thesis_engine.py` — passed.

## Behavior change
- New backend-only translation contract is available as a module for future API/UI integration.
- No score math changes, no allocation/deploy changes, no LLM behavior changes.

---

## Last change
Intel v2 PR-6: valuation context audit + safe backend-only valuation field mapping (PR: "feat(intel-v2-pr6): add safe valuation field coverage for ps_ttm and ev_ebitda").

## Files touched
- `v2/backend/app/services/agents/data_sources.py` — yfinance fundamentals payload now includes additive backend-only valuation fields when available: `ps_ttm` (`priceToSalesTrailing12Months`) and `ev_ebitda` (`enterpriseToEbitda`).
- `v2/backend/app/services/intelligence/thesis_mapper.py` — added exact deterministic pass-through mappings: `ps_ttm -> ps_ttm` and `ev_ebitda -> ev_ebitda` (no conversion, no proxy derivation).
- `v2/backend/tests/test_thesis_mapper.py` — added focused mapping/omission tests for `ps_ttm` and `ev_ebitda` including NaN/None omission behavior.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## PR-6 audit findings (valuation context)
- **Found via existing provider path (`yfinance info`)**: trailing PE (`trailingPE`), forward PE (`forwardPE`), PEG (`pegRatio`), price-to-sales (`priceToSalesTrailing12Months`), EV/EBITDA (`enterpriseToEbitda`), sector, industry.
- **Mapped now (safe/exact)**: `trailing_pe`, `forward_pe`, `peg`, `ps_ttm`, `ev_ebitda`.
- **Found but deferred**: price-to-free-cash-flow (no stable exact provider key currently wired), peer/sector medians, own-history valuation baselines.
- **Not present as reliable context in current pipeline**: true peer set with medians and historical valuation baselines for cheap/expensive labels.

## Explicit semantic guardrails upheld
- No “cheap/expensive” label from PE-only or PEG-only.
- No sector-string-only peer baseline inference.
- No synthetic peer medians or historical ranges.
- Raw valuation metric names remain backend-only inputs.

## QA scope completed
- `cd v2/backend && pytest -q tests/test_thesis_mapper.py tests/test_thesis_engine.py` — passed.

## Behavior change
- Additive backend-only valuation coverage: when provider supplies P/S and EV/EBITDA, thesis scoring now receives exact `ps_ttm` and `ev_ebitda` inputs.
- No user-facing Intel/Deploy wording changes.
- No frontend/UI/LLM/Deploy behavior changes.

## Last change
Deploy Logic v2 PR 2: wire deterministic deployment engine into the live Deploy recommendation path (PR: "feat(deploy-v2-pr2): wire deployment_engine into live allocation router path").

## Files touched
- `v2/backend/app/routers/allocation.py` — imported `classify_deployment` + `DeploymentDecision` from `deployment_engine`; added `deployment_v2` parameter to `_plan_to_dict()`; call `classify_deployment()` in route handler after adaptive layer; per-ticker `immediate_amount`/`reserve_amount` now come from v2 `per_ticker_allocations`; plan_block gains `deploy_now_amount`, `reserve_amount`, `deployment_mode_v2`, `deployment_confidence`, `deployment_reason`, `cash_drag_penalty_applied`, `reserve_reason` and overrides `recommended_deploy_amount`/`cash_reserve` with v2 canonical values; top-level `deployment_v2` block added.
- `v2/frontend/src/app/api/deposit-plan/route.ts` — added `DeploymentV2Block` and `ReserveTriggerV2` types; extended `AllocationPlanPayload.plan` with v2 fields; forwards `deployment_v2` block and all v2 plan fields in the JSON response.
- `v2/frontend/src/lib/api.ts` — added v2 fields to `DepositPlanResult.plan` (`deploy_now_amount`, `reserve_amount`, `deployment_mode_v2`, `deployment_confidence`, `deployment_reason`, `cash_drag_penalty_applied`, `reserve_reason`); added `deployment_v2?: DeploymentDecisionV2 | null` to `DepositPlanResult`.
- `v2/backend/tests/test_deployment_wiring.py` — **new test file**. 16 focused tests verifying the live wiring contract: full_deploy deploys full $900 with no trigger; staged only reserves with trigger; per-ticker sums match deploy_now_amount; hard no-reserve-without-trigger rule upheld; backward-compat fields preserved; Watch-tier cap reflected in immediate_amount.
- `v2/progress_log.md` — concise entry added.

## QA scope completed
- `pytest -q v2/backend/tests/test_deployment_engine.py` — 32 passed (no regressions).
- `pytest -q v2/backend/tests/test_adaptive_deployment.py` — 32 passed (no regressions).
- `pytest -q v2/backend/tests/test_allocation_engine.py` — 17 passed (no regressions).
- `pytest -q v2/backend/tests/test_deployment_wiring.py` — 16 new wiring tests passed.
- Total: 97 tests, 0 failures.
- No Supabase SQL required.
- No LLM calls added or changed.
- `adaptive_deployment.py` and its tests untouched.

## Behavior change
- **Live**: `GET /api/v1/allocation/plan` now calls `classify_deployment()` for every request. The `deployment_v2` block is present in the response with `deploy_now_amount`, `reserve_amount`, `deployment_mode` (v2 labels), `deployment_confidence`, `reserve_trigger`, `risks`, and `adjustments_applied`.
- **Plan-level canonical amounts**: `plan.recommended_deploy_amount` and `plan.cash_reserve` are now driven by the v2 engine (was adaptive). Old Deploy UI reads same field names and gets v2 values transparently.
- **Per-ticker**: `immediate_amount` and `reserve_amount` per allocation row now come from v2's `per_ticker_allocations` (imm_frac × amount). Adaptive's `staging_instruction` and `execution_timing` are preserved alongside.
- **Backward compat**: all pre-existing response fields (`adaptive`, `regime`, `plan.deployment_mode`, `plan.deploy_percentage`, `plan.cash_reserve`, `plan.recommended_deploy_amount`) remain in the response. The `adaptive` block retains its own values for audit/debug.
- **Hard reserve rule**: enforced at the classify_deployment call site — reserve > $25 is only permitted if `_generate_reserve_trigger` returns a specific trigger; otherwise mode is forced to `full_deploy` and reserve to 0.

## Known issues / next steps
- Frontend Deploy UI still reads `plan.recommended_deploy_amount` (unchanged field name); it now receives the v2 value. No UI redesign needed.
- `npm install` not run in CI; frontend type check requires deployment environment.
- `adaptive_deployment.py` remains in codebase as a fallback and for its behavior profile / staging instruction details. Migration of old mode labels (`full/partial/defensive/wait`) to v2 labels can follow separately.

## Debug notes
- `classify_deployment()` is wrapped in a broad exception guard in the router — if it fails for any reason, `deployment_v2=None` is used and the response falls back to adaptive-only values.
- `_plan_to_dict` applies v2 values after the adaptive block, so v2 always wins for `recommended_deploy_amount` and `cash_reserve` when both are present.
- Score formula and constants are unchanged from PR 1 (`deployment_engine.py`).

---

## Previous change
Deploy Logic v2 PR 1: deterministic deployment-mode classifier, output schema, and focused backend tests (PR: "feat(deploy-v2-pr1): deterministic deployment-mode classifier").

## Files touched
- `v2/backend/app/services/deployment_engine.py` — **new module**. Pure deterministic deployment mode classifier. Emits `DeploymentDecision` with `deployment_mode ∈ {full_deploy, staged_deploy, defensive_reserve, skip_or_wait}`, `deploy_now_amount`, `reserve_amount`, `deployment_confidence`, `reserve_trigger` (required when reserve > $25), `per_ticker_allocations`, `risks`, `data_quality`, `evaluation_notes_for_future_decision_log`, `deployment_score`, `adjustments_applied`.
- `v2/backend/tests/test_deployment_engine.py` — **new test file**. 32 focused tests covering: full deploy (no reserve trigger), hard reserve trigger rule, cash drag penalty, concentration risk, WATCH-tier cap, deploy-now denominator correctness, no generic reserve text, data quality confidence, edge cases.
- `v2/frontend/src/lib/api.ts` — added `DeploymentModeV2`, `DeploymentDecisionV2`, `ReserveTriggerV2`, `PerTickerDeploymentV2`, `TickerRole` types. Old `DeploymentMode` type unchanged (backward compatible).
- `v2/progress_log.md` — concise entry added.

## QA scope completed
- `pytest -q v2/backend/tests/test_deployment_engine.py` passed (32 tests).
- `pytest -q v2/backend/tests/test_adaptive_deployment.py v2/backend/tests/test_allocation_engine.py` passed (49 tests — no regressions).
- No UI files changed.
- No API contracts changed (new types are additive).
- No Supabase SQL required.
- No LLM calls added or changed.
- `adaptive_deployment.py` and its tests untouched.

## Behavior change
- **New**: `classify_deployment()` function in `deployment_engine.py` implements deterministic scoring: BASE(70) + structural_bonus(0-15) + quality_bonus(0-10) + cash_drag_bonus(0-20) - concentration_penalty(0-20) - regime_penalty(0-25) - data_quality_penalty(0-15). Mode thresholds: full≥70, staged≥50, defensive≥30, skip<30.
- **Hard rule**: `reserve_amount > $25` requires a valid non-generic trigger; otherwise reserve forced to 0 and mode forced to `full_deploy`.
- **Cash drag**: when prelim unallocated reserve > $25 and no strong trigger, cd_bonus added proportional to reserve ratio. When cash == plan total (no unallocated excess), the hard trigger rule enforces this instead.
- **WATCH cap**: LOW conviction tickers capped at 25% of total plan amount.
- **Existing adaptive_deployment.py not changed** — it continues to be the production deployment engine. The new `deployment_engine.py` is a parallel module, ready to be wired in via a subsequent PR.

## Known issues / next steps
- `deployment_engine.py` is not yet wired into the allocation router (`app/routers/allocation.py`). Wiring is PR 2 scope.
- `adaptive_deployment.py` uses old mode labels (`full/partial/defensive/wait`) — these are kept for backward compatibility and will be migrated in PR 2.
- `npm install` not run in CI; frontend type check requires deployment environment.

## Debug notes
- Score constants centralized at top of `deployment_engine.py` (BASE_DEPLOYMENT_SCORE, FULL_DEPLOY_SCORE, etc.) — change them there only.
- `_generate_reserve_trigger` has 4 priority paths: near-cap → Watch-tier → risk-off → concentration. Returns `None` only when all 4 are inapplicable (very rare in practice).
- The cash drag bonus uses `prelim_reserve = max(0, cash - plan_total)` as the scale signal — this is unallocated excess cash, not the staged portion of the plan.

---

## Previous change
Deploy Step 3 + Decision History refactor: correct deploy-now amount semantics, execution status, copy, and UI split (PR: "refactor(deploy-step3): correct amount semantics + split Decision History card").

## Files touched
- `v2/frontend/src/app/dashboard/deposits/page.tsx` — Replaced `DecisionLogMemoryPanel` with two-card layout: Card A (Step 3 Execute & Record) and Card B (Decision History). Fixed `buildInitialActualDecisions` call site to pass `adjustedAmountsForLog` so "Use AI Plan" prefills actual rows from deploy-now amount not full deposit. Fixed execution copy to use deploy-now denominator. Added `executionStatusLabel`/`executionStatusCls`/`buildExecutionCopy` helpers. Added `DecisionHistoryEntry` component with expandable per-ticker actuals and performance windows.
- `v2/frontend/src/lib/decision-log.ts` — Fixed `buildInitialActualDecisions` signature to accept optional `adjustedAmounts: Map<string, number>`. Added `deriveExecutionStatus` (uses deploy-now denominator). Exported `ExecutionStatus` type.
- `v2/frontend/src/lib/decision-log.test.ts` — Added 7 new tests: adjusted-amount sums to deploy-now; fallback to rec.amount; skipped/fully_executed/partially_executed/modified status derivation; denominator correctness ($725 actual vs $900 deposit = fully_executed).
- `v2/progress_log.md` — Concise entry added.

## QA scope completed
- No node_modules in CI environment; `npm test` cannot run. Tests are authored and will run in deployment environment.
- No backend files touched.
- No API contracts changed.
- No recommendation/allocation algorithm changed.
- No Supabase SQL required.
- No LLM calls added or changed.

## Behavior change
- "Use AI Plan" now prefills rows summing to deploy-now amount (e.g. $725), not full deposit ($900).
- Execution status badge derives from actual vs deploy-now (not deposit).
- Execution copy: "Executed $X of $Y planned now. Reserved $Z from your $D deposit."
- Decision History is a separate card (Card B) below Step 3. Each entry shows date, status badge, deposit/invested/reserve, ticker actuals. Expand for performance vs AI (7d/30d/90d) and deviation detail.
- Performance/insights moved from Step 3 editor to Decision History expand section.

## Known issues
- `npm install` not run in CI; full build verification requires deployment environment.
- `tsc --noEmit` would show only pre-existing errors (missing node_modules types).

## Next likely task
- Playwright snapshot baseline update for the new two-card Step 3 layout.
- Optional: further polish of Decision History (e.g. filter by status, sort controls).

## Debug notes
- `deriveExecutionStatus` tolerance is $0.51 to handle floating-point allocation math.
- `buildInitialActualDecisions` is backward-compatible: without `adjustedAmounts` it uses `rec.amount` (old behavior) — safe for any callers that don't pass adjusted amounts.
- Rehydration `useEffect` guards on `savedLog` being null before applying `matchingRecentLog`, preventing overwrite of in-session edits.

---

## Previous change
Frontend UI foundation pass: elite intelligence design system (PR: "Investing UI Foundation: elite intelligence design system pass").

## Files touched
- `v2/frontend/tailwind.config.ts` — Extended token set: `accent-blue`, `accent-purple`, `positive`, `negative`, `caution`, `neutral`, `surface-hover`, `border-strong`, box-shadow tokens, `2xs` font size, `label`/`widest2` letter-spacing
- `v2/frontend/src/app/globals.css` — New component layer primitives: badge system (`badge`, `badge-positive`, `badge-negative`, `badge-caution`, `badge-info`, `badge-accent`, `badge-purple`, `badge-surface`), action badges (`action-badge-*`), data state colors (`data-positive/negative/caution/neutral`), table primitives (`data-table-header/row/footer`), button primitives (`btn-primary/secondary/ghost/danger`), typography helpers (`metric-label`, `section-header`, `data-value*`, `ticker-symbol`), page shell helpers (`page-header`, `page-main`), block helpers (`risk-block`, `info-block`), `intel-card`. Kept all existing classes. Font-feature-settings added to body.
- `v2/frontend/src/components/navigation/BottomNav.tsx` — Active indicator (top hairline on mobile, left border on desktop), platform name two-line treatment, tighter icon labels, v2.0 footer in SideNav
- `v2/frontend/src/components/holdings/HoldingsList.tsx` — Filter pills use `badge-surface` / accent pattern, holdings list uses `data-card` + `divide-y`, improved `ticker-symbol` / `metric-label` usage, `+` prefix on gains
- `v2/frontend/src/components/holdings/PortfolioSummaryCard.tsx` — SummaryPill uses `data-card`, `metric-label`, `text-positive/negative` data state. PriceHealthBadge uses new badge classes. Day change shows `+` prefix.
- `v2/frontend/src/components/cards/InsightCard.tsx` — `ACTION_STYLES` expanded with `badge` key using `action-badge-*` classes. Card uses `intel-card`. Footer pills use `badge-*` system. Rationale has left-border accent treatment.
- `v2/frontend/src/components/ui/EmptyState.tsx` — Horizontal rule divider, tighter spacing

## QA scope completed
- Build environment (no node_modules) prevents full `next build`; tsc run confirms all errors are pre-existing environment issues (missing React/Next type declarations), none introduced by this PR
- No backend files touched
- No API contracts changed
- No business logic changed
- No allocation math changed (Deploy tab)
- No Intel reasoning changed
- No decision logging changed
- No auth changed
- No routing changed
- No Supabase SQL required

## Behavior change
- Visual only. All existing flows, data, and calculations are identical.
- App uses new design tokens and shared component classes for consistency.

## Known issues
- `npm install` not run in CI environment, so full build verification requires deployment environment
- `tsc --noEmit` shows only pre-existing errors (missing node_modules types)

## Next likely task
- Page-specific polish using the new primitives (Deploy table rows, Intel page filter pills, DRIP page)
- Optional: apply `data-table-header/row` to AllocationBreakdownTable in deposits page
- Optional: Playwright snapshot baseline update

## Debug notes
- All new CSS classes are additive; no existing classes removed or renamed
- `card-glass` and `card-elevated` kept intact for backward compat
- `pnl-positive` / `pnl-negative` kept; `data-positive` / `data-negative` added as semantic aliases

## Last change
Decision Log Performance v1: windowed evaluation statuses + minimal Deploy memory surfacing.

## Files touched
- `v2/backend/app/services/decision_log_service.py` — Added window-level performance rollups (`7d`/`30d`/`90d`) with status model (`pending`, `ready`, `insufficient_data`, `unavailable`) and included them under `performance_snapshot.windows`.
- `v2/backend/tests/test_decision_performance.py` — Added tests for window status transitions and unavailable data handling while preserving existing baseline/missing-price checks.
- `v2/frontend/src/lib/api.ts` — Extended `DecisionMemoryLog.performance_snapshot` typing to include new status variants and `windows` structure.
- `v2/frontend/src/app/dashboard/deposits/page.tsx` — Minimal Step 3 UI update to display compact 7d/30d/90d results/status without redesigning Deploy.
- `v2/progress_log.md` — Added concise project progress note.

## QA scope completed
- `pytest -q v2/backend/tests/test_decision_performance.py` passed (6 tests).
- No recommendation/allocation algorithm changes.
- No Intel tab logic changes.
- No Supabase schema migration required for this patch (JSON snapshot extension only).

## Behavior change
- Decision logs now expose time-window evaluation state explicitly so frontend can show pending/unavailable instead of ambiguous or misleading 0% outputs.
- Deploy execution cockpit remains intact; update is additive and compact.

## Known limitations
- Window returns are based on available baseline-vs-current prices; no separate historical candles are fetched per window.
- If price points are missing, window status reports `unavailable` and does not fabricate return percentages.
