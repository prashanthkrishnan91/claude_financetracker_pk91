## 2026-05-05 — Intel Card Narrative Contract v1 (Level 3 / Sev 1)

- Production context: total_cards=34, v2_visible_action_counts={BUY:11,HOLD:23}, evidence_quality_status_counts={PRESENT:34}.
- Root cause (two-part):
  1. _derive_intel_posture rule 5.5 returned "Review" for BUY+insufficient → build_posture_reason("Review") → "Reviewing before taking action — the setup is interesting but not yet complete." as the PRIMARY Evidence Check text on BUY cards.
  2. _build_caveat WATCH fallback → "Treat this as an early signal, not a complete picture." as secondary Evidence Check text on BUY cards when r2.action.posture=WATCH and n_trusted>=1.
- Fix:
  - _derive_intel_posture rule 5.5 removed; rule 5 simplified to: if action == "BUY": return "Add Candidate" (regardless of insufficient_data).
  - build_intel_card_narrative_contract() added to reasoning_v2_plain_english.py: deterministic helper keyed on VISIBLE action. Returns evidence_summary (→ posture_reason) and final_takeaway (→ caveat). No HOLD/wait language can appear on BUY cards by construction.
  - detect_intel_card_conflict() added: pure conflict detector; flags forbidden phrases per action.
  - recommendation_engine.py card assembly: contract applied after gate code; intel_read_dict posture_reason and caveat overridden; narrative_contract stored; intel_card_narrative_contract_summary INFO log emitted per run.
  - api.ts: narrative_contract optional field added to IntelRead.
- Invariant: no rendered card has action/copy conflict.
- Files changed: reasoning_v2_plain_english.py, recommendation_engine.py, api.ts, tests/test_v3_intel_card_narrative_contract.py (new, 67 tests).
- Tests: 257 pass (67 new + 190 existing).
- Supabase SQL: No.
- Validation:
  - Open Intel → BUY cards: Evidence Check must say measured-buy language, not "Reviewing before taking action" or "early signal".
  - VGT/ETF BUY: must say "Regular contribution target".
  - Railway log: intel_card_narrative_contract_summary conflict_count=0.
  - Railway log: intel_card_narrative_contract_conflicts warning absent.

## Last change
Intel v3 PR 13: Sev 1 all-HOLD Intel collapse fix.

## Severity
Level 3 — production-visible product correctness failure. Every Intel card showed HOLD despite rich evidence signals.

## Production failure (before this PR)
- v2_visible_action_counts: BUY 0 / HOLD 34 / TRIM 0 / SELL 0
- evidence_quality_status_counts: {"WEAK": 34}
- Cards showed "Stay on watchlist… Missing: growth, risk" for every card including those with rich quality/valuation/momentum evidence.

## Root cause
`intel_read.insufficient_data=True` was used as a binary global HOLD gate in **three places**. The `scorecard.status=INSUFFICIENT_DATA` (triggered when growth/risk axes are missing from the thesis engine scorecard) propagated into `intel_read.insufficient_data=True`. All three gates checked `if insufficient or n_trusted == 0:` — so even cards with 2-3 published dimensions (quality, valuation, momentum) triggered the HOLD gate, because `insufficient=True` dominated over `n_trusted > 0`.

This is why PR 12 key fix (trusted_dimensions → trusted_signals) did not fix production: the `insufficient` flag overrode the trusted_signals count regardless.

Three collapse points:
1. **Card assembly gate** (`recommendation_engine.py` lines 1833–1869): forced BUY→HOLD for any card with `insufficient_data=True`, even with 2-3 trusted signals.
2. **V3 adapter** (`existing_signal_adapter._derive_evidence_quality`): returned `AxisBand.THIN` for `insufficient=True` regardless of trusted count → v3 `decide()` produced HOLD.
3. **Data truth** (`data_truth_v1.classify_evidence_signals`): returned WEAK for `insufficient=True` regardless of trusted count → `evidence_quality_status_counts: {"WEAK": 34}`.

## Fix (Option B — v2 gate fix; v3 shadow stays shadow-only)
Changed all three gates from `if insufficient or n_trusted == 0:` to `if n_trusted == 0:`. Missing axes suppress only themselves; zero trusted signals is the only global HOLD trigger.

**Files changed:**
- `v2/backend/app/services/recommendation_engine.py`: visible gate — n_trusted==0 → global collapse; n_trusted>=1 → preserve action, downgrade conviction only (no copy replacement).
- `v2/backend/app/services/intelligence/v3/existing_signal_adapter.py`: `_derive_evidence_quality` — n_trusted==0 → THIN; n_trusted>=1 → OK/STRONG.
- `v2/backend/app/services/intelligence/v3/data_truth_v1.py`: `classify_evidence_signals` — n_trusted==0 → WEAK; n_trusted>=1 → PRESENT/MEDIUM or PRESENT/HIGH.
- `v2/backend/tests/test_v3_evidence_quality_source_mapping.py`: updated one test that was asserting the old (incorrect) behavior.
- `v2/backend/tests/test_v3_intel_collapse_fix.py` (new): 30 production-shaped tests.

## Expected production change
With the fix deployed:
- Cards with quality/valuation/momentum published (n_trusted≥3) + analyst BUY → BUY action visible.
- Cards with partial evidence (n_trusted=1-2) + analyst BUY → BUY action preserved, conviction MEDIUM/LOW.
- Cards with zero trusted signals → HOLD (unchanged, correct).
- evidence_quality_status_counts will contain PRESENT entries (not all WEAK).
- v3 shadow action diversity will emerge matching v2 visible diversity.

## No env flag needed
The v2 gate is now correct by default. No Railway env flag change required.

## Explicit non-changes
- No visible action labels changed beyond BUY/HOLD/TRIM/SELL.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No frontend redesign.
- No raw metric keys in UI.
- No legacy/posture action labels.
- No real user/account data in fixtures.
- V3 shadow stays shadow-only (not yet visible).

## Test results
372 v3 tests pass (342 existing + 30 new in test_v3_intel_collapse_fix.py).

## Next intended step
Deploy to Railway (no env flag needed — fix is active by default). Confirm with one production cycle:
- visible filter distribution shows non-zero BUY/TRIM/SELL counts
- evidence_quality_status_counts has PRESENT entries
- no legacy/posture labels visible
- no Deploy behavior change

---

## Last change (PR 12)
Intel v3 PR 12: backend-only evidence-quality source mapping calibration.

## Severity
Level 2 — production Data Truth mapping / root-cause calibration.

## Production observation (after PR 11)
With INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED=true, production portfolio showed:
- evidence_quality_status_counts: {"WEAK": 34}
- evidence_quality_trust_counts: {"LOW": 34}
- All 34 cards returned WEAK/LOW despite having rich intel_read with multiple trusted signals.
- guardrail_evaluated_count: 34 (wiring from PR 11 confirmed working).
- buy_high_conviction_pre_guardrail_count: 0 (no HIGH-conviction BUY — all HOLD).

## Root cause
`classify_evidence_signals()` in `data_truth_v1.py` read `intel_read.get("trusted_dimensions")`
but the production intel_read dict (built by `build_intel_read()` in `reasoning_v2_plain_english.py`)
uses key `"trusted_signals"`. Because `"trusted_dimensions"` was never found, n_trusted was
always 0, and every card with intel_read returned WEAK/LOW regardless of actual signal richness.

The same bug existed in `_derive_evidence_quality()` in `existing_signal_adapter.py`.

Test helpers in 6 test files also used `"trusted_dimensions"`, so tests passed but tested
the wrong production field shape — concealing the bug.

## Fix
1. `data_truth_v1.py` — `classify_evidence_signals()`:
   - Fixed `intel_read.get("trusted_dimensions")` → `intel_read.get("trusted_signals")`.
   - Added `analyst_used_fallback: Optional[bool] = None` parameter.
   - When `analyst_used_fallback is True` and result would be PRESENT/HIGH (≥3 signals or
     data_quality_label="HIGH"), caps trust to PRESENT/MEDIUM with reason_code
     "field_present_fallback_capped". This conservatively prevents fallback LLM outputs
     from defeating the evidence-quality guardrail.

2. `existing_signal_truth_adapter.py` — `evaluate_card_signals_truth()`:
   - Added `analyst_used_fallback: Optional[bool] = None` parameter.
   - Passes through to `classify_evidence_signals()`.

3. `existing_signal_adapter.py` — `_derive_evidence_quality()`:
   - Fixed `intel_read.get("trusted_dimensions")` → `intel_read.get("trusted_signals")`.
   - `build_truth_aware_decision_input()`: added `analyst_used_fallback` parameter, passes
     to `evaluate_card_signals_truth()`.

4. `shadow_projection.py` — `project_shadow_from_card_signals()`:
   - Added `analyst_used_fallback: Optional[bool] = None` parameter.
   - Passes to `build_truth_aware_decision_input()`.

5. `recommendation_engine.py` — `_v3_shadow_projection()`:
   - Passes `analyst_used_fallback=card.analyst_used_fallback` to `project_shadow_from_card_signals()`.

6. Test fixtures — 6 test files updated:
   - All `"trusted_dimensions"` in intel_read helpers changed to `"trusted_signals"`.
   - Updated: `test_v3_data_truth.py`, `test_v3_truth_aware_adapter.py`,
     `test_v3_signal_hydration.py`, `test_v3_evidence_quality_guardrail.py`,
     `test_v3_shadow_projection.py`, `test_v3_decision_policy.py`.

7. New `tests/test_v3_evidence_quality_source_mapping.py` — 37 production-shaped tests:
   - Section 1: trusted_signals key correctness (including regression proving trusted_dimensions = WEAK).
   - Section 2: analyst_used_fallback cap boundary conditions.
   - Section 3: data_quality_label fallback path.
   - Section 4: evaluate_card_signals_truth propagation.
   - Section 5: project_shadow_from_card_signals production-shaped fixtures.
   - Section 6: mixed 6-card synthetic portfolio — non-uniform evidence quality.
   - Section 7: 34-card all-HOLD production regression — non-uniform after fix.

## Evidence-quality mapping contract (PR 12)
- PRESENT/HIGH: ≥3 trusted_signals in intel_read AND analyst_used_fallback is not True.
  OR data_quality_label="HIGH" AND analyst_used_fallback is not True.
- PRESENT/MEDIUM: 1-2 trusted_signals, OR ≥3 signals with analyst_used_fallback=True,
  OR data_quality_label="HIGH" with analyst_used_fallback=True,
  OR data_quality_label="MEDIUM".
- WEAK/LOW: insufficient_data=True, OR 0 trusted_signals, OR data_quality_label="LOW".
- MISSING: no intel_read and no data_quality_label, OR unrecognized label.

## Explicit non-changes
- No visible UI behavior change.
- No API schema change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No threshold tuning of visible behavior.
- No visible policy-gating added.
- No real user/account data in fixtures.
- PR 9 guardrail logic unchanged; mapping calibration means guardrail now activates for
  PRESENT/MEDIUM cards where it previously never fired (because all were WEAK).

## Test results
341 v3 tests pass:
- 37 new in `test_v3_evidence_quality_source_mapping.py`
- 304 existing v3 tests (decision_policy + shadow_projection + truth_aware_adapter +
  data_truth + signal_hydration + evidence_quality_guardrail + guardrail_impact_observability)

## Next intended step
Re-enable INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED=true and run one production cycle to
confirm evidence_quality_status_counts now contains PRESENT entries (not uniformly WEAK).
Observe guardrail_applied_reasons for the PRESENT/MEDIUM → HIGH-conviction cap rate.
Only then consider whether threshold tuning or policy changes are warranted in PR 13+.

---

## Last change (PR 11)
Intel v3 PR 11: backend-only v3 truth diagnostics wiring fix / signal hydration audit.

## Severity
Level 2 — production shadow wiring / root-cause fix.

## Production observation
After enabling INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED=true, one production Railway cycle showed:
- safe_axis_count=0, unsafe_axis_count=0
- evidence_quality_status_counts={}, evidence_quality_trust_counts={}
- guardrail_evaluated_count=0
- dominant_truth_reason=none

All despite 34 real cards having analyst_action, data_quality_label, intel_read, conviction_level and other truth-relevant fields.

## Root cause
`_v3_shadow_projection(card)` in `recommendation_engine.py` was a stale implementation added before PRs 7–10 landed. It called `_v3_shadow_decide()` → `build_decision_input_from_card()` (non-truth-aware) and returned a dict **without** `truth_diagnostics`. The truth-aware `project_shadow_from_card_signals()` in `shadow_projection.py` existed since PR 7 but was never wired to replace this function. `summarize_truth_aware_suppression()` and `summarize_guardrail_impact_observability()` both look for `truth_diagnostics` per card and found nothing → all counts were zero.

## Fix
- `v2/backend/app/services/recommendation_engine.py`:
  - Added `project_shadow_from_card_signals` to the import from `shadow_projection`.
  - Replaced `_v3_shadow_decide()` + old `_v3_shadow_projection()` body with a single thin `_v3_shadow_projection(card)` that delegates to `project_shadow_from_card_signals()` with InsightCard fields. All diagnostic keys are now populated including `truth_diagnostics` with `safe_axis_count`, `unsafe_axis_count`, `suppressed_axis_reasons`, and `buy_conviction_guardrail`.
- `v2/backend/tests/test_v3_signal_hydration.py` (new):
  - 25 tests with production-shaped synthetic fixtures covering:
    1. `truth_diagnostics` key always present for real card shapes.
    2. `evidence_quality_status_counts` nonempty when guardrail evaluated.
    3. `safe_axis_count` + `unsafe_axis_count` = 5 axes (invariant).
    4. Guardrail capping for BUY/HIGH + MEDIUM evidence trust.
    5. 34-card all-HOLD batch regression (mirrors production cycle).
    6. Visible action unchanged.
    7. Fail-soft for None/partial fields.

## Fixed contract
After PR 11, one production cycle with INFO logs enabled should emit:
- `safe_axis_count` > 0 for real cards with evidence fields.
- `evidence_quality_status_counts` nonempty.
- `guardrail_evaluated_count` = total projected cards (guardrail is always evaluated).

## Explicit non-changes
- No visible UI behavior change.
- No API schema change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No threshold tuning.
- No visible policy-gating added.
- No real user/account data in fixtures.

## Test results
- `pytest -q tests/test_v3_signal_hydration.py tests/test_v3_shadow_projection.py tests/test_v3_truth_aware_adapter.py tests/test_v3_guardrail_impact_observability.py tests/test_v3_evidence_quality_guardrail.py tests/test_recommendation_engine.py tests/test_v3_data_truth.py tests/test_v3_decision_policy.py`
- Result: 400 passed.

## Next intended step
Enable INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED=true and run one production cycle to confirm safe_axis_count, evidence_quality_status_counts, and guardrail_evaluated_count are now populated. Then decide if threshold tuning or policy changes are warranted in a separate PR.

---

## Last change
Intel v3 PR 10: backend-only guardrail impact observability for PR 9 evidence-quality BUY conviction guardrail.

## Severity
Level 1 — backend observability/control follow-up only.

## Assumptions and success criteria
- Reuse existing portfolio-level v3 shadow summary path (PR 3/4/8) and keep default INFO logging disabled.
- Aggregate only stable, non-sensitive PR 9 guardrail diagnostics already produced per card.
- Emit one batch summary only (no per-card INFO spam).
- Preserve visible recommendations/actions/API behavior and keep guardrail shadow-only.

## Fix
- Extended `v2/backend/app/services/intelligence/v3/shadow_projection.py` with
  `summarize_guardrail_impact_observability(diagnostics)` (pure deterministic helper):
  - `guardrail_evaluated_count`
  - `buy_high_conviction_pre_guardrail_count`
  - `buy_conviction_capped_count`
  - `buy_remained_buy_after_cap_count`
  - `guardrail_applied_reasons`
  - `evidence_quality_status_counts`
  - `evidence_quality_trust_counts`
  - `v3_shadow_action_counts`
  - `v3_shadow_conviction_counts`
- Updated `v2/backend/app/services/recommendation_engine.py`:
  - `_build_v3_shadow_info_summary()` now merges PR 3 summary + PR 8 truth suppression + PR 10 guardrail impact aggregates.
  - Reused existing env flag `INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED` (default unchanged: disabled).
  - Logging path remains one structured portfolio summary per batch.
- Added focused tests in `v2/backend/tests/test_v3_guardrail_impact_observability.py`.
- Updated recommendation engine observability tests to assert stable key contract including guardrail-impact fields.

## Guardrail-impact observability contract (PR 10)
- Aggregate-only backend payload. No raw card payloads, metric keys, account values, holdings, user identifiers, provider payloads, or LLM text.
- `projection_failures`, `hold_collapse_risk_count`, and `honest_hold_count` remain sourced from existing PR 3 summary.
- Guardrail impact uses only `truth_diagnostics.buy_conviction_guardrail` fields from PR 9.

## Explicit non-changes
- No visible UI behavior change.
- No API schema change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No visible policy-gating added.

## Test results
- `pytest -q tests/test_v3_guardrail_impact_observability.py tests/test_recommendation_engine.py tests/test_v3_evidence_quality_guardrail.py tests/test_v3_shadow_projection.py tests/test_v3_truth_aware_adapter.py tests/test_v3_data_truth.py tests/test_v3_decision_policy.py`
- Result: 375 passed.

## Next intended step
Observe one full production cycle with INFO flag enabled to baseline cap rate and evidence-quality distribution, then decide if any non-observability policy changes are warranted in a separate PR.

---

## Last change
Intel v3 PR 9: shadow-only evidence-quality BUY conviction guardrail.

## Severity
Level 2 — shadow-only v3 policy guardrail. Changes v3 shadow conviction semantics for BUY/HIGH when evidence quality is not PRESENT/HIGH-trust. Backend-only dark launch; no visible recommendations changed.

## Assumptions and success criteria
- "Strong enough" evidence = `DataTruthFinding.status == PRESENT` AND `trust_level == HIGH` for the evidence_quality axis.
- This maps exactly to ≥3 trusted dimensions in intel_read OR `data_quality_label="HIGH"`.
- Guardrail is shadow-only: applied post-decide in shadow_projection.py, not in the core decision_policy_v1.py kernel.
- SELL/TRIM protective actions are never affected (guardrail fires only when action=BUY and conviction=HIGH).
- BUY action is preserved at MEDIUM conviction when guardrail fires — not collapsed to HOLD.
- All existing visible v2 actions/cards/API/Deploy remain completely unchanged.

## Fix
- Added `v2/backend/app/services/intelligence/v3/buy_conviction_guardrail.py`:
  - `_evidence_is_high_trust(ev_summary)` — True only when evidence_quality finding is PRESENT/HIGH.
  - `apply_buy_conviction_guardrail(action, conviction, evidence_quality_truth)` → `(ConvictionV3, diagnostics_dict)`.
  - Guardrail fires when: action=BUY AND conviction=HIGH AND evidence not PRESENT/HIGH-trust.
  - When fired: caps conviction from HIGH to MEDIUM.
  - Stable diagnostic keys: `buy_high_conviction_guardrail_applied`, `buy_conviction_capped_reason`, `evidence_quality_truth_status`, `evidence_quality_trust_level`, `pre_guardrail_conviction`, `post_guardrail_conviction`.
  - Pure function — no IO, DB, LLM, provider calls.
- Modified `v2/backend/app/services/intelligence/v3/shadow_projection.py`:
  - Imports `apply_buy_conviction_guardrail`.
  - After `decide()`, extracts evidence_quality AxisTruthSummary from truth_summaries.
  - Calls `apply_buy_conviction_guardrail()` and uses post-guardrail conviction as `v3_shadow_conviction`.
  - Adds `buy_conviction_guardrail` sub-dict to `truth_diagnostics`.
- Added `v2/backend/tests/test_v3_evidence_quality_guardrail.py` — 57 tests across 7 test classes.

## Evidence-quality guardrail contract (PR 9)
- Guardrail axis: `evidence_quality` (from `classify_evidence_signals()` finding).
- "High-trust" = `DataTruthFinding.status == PRESENT AND trust_level == HIGH`.
- PRESENT/HIGH maps to: intel_read with ≥3 trusted dimensions OR data_quality_label="HIGH".
- All other states (PRESENT/MEDIUM, WEAK, MISSING, STALE, UNAVAILABLE, CONFLICTING, or no summary) are considered not high-trust → guardrail fires for HIGH-conviction BUY.
- Guardrail cap: HIGH → MEDIUM (preserves BUY action at lower conviction).
- Shadow diagnostics: `truth_diagnostics.buy_conviction_guardrail` sub-dict (stable, aggregate-safe keys).
- SELL/TRIM are always independent of this guardrail.
- v2 visible action never mutated.

## Dark-launch safety notes
- No visible v2 recommendation, action, card, or API behavior changed.
- No API schema, frontend, Deploy, SQL, provider, or LLM changes.
- v3 shadow conviction may change for cards where evidence is PRESENT/MEDIUM trust and upstream conviction was HIGH (active case: OK evidence + HIGH conviction → now BUY/MEDIUM in shadow).
- All PR 2/3/4/5/6/7/8 stable diagnostic keys unchanged.
- `buy_conviction_guardrail` sub-dict is additive to existing `truth_diagnostics`.

## Explicit non-changes
- No visible UI behavior change.
- No API schema change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No real user/account data in fixtures.
- No mutation of visible v2 card actions.
- No frontend/API/Deploy/schema files changed.

## Test results
- 57 new tests in `test_v3_evidence_quality_guardrail.py` — all pass.
- 219 existing v3 tests (decision_policy + shadow_projection + truth_aware_adapter + data_truth) — all pass.
- Total: 276 tests pass.
- Test command: `cd v2/backend && pytest tests/test_v3_evidence_quality_guardrail.py tests/test_v3_shadow_projection.py tests/test_v3_truth_aware_adapter.py tests/test_v3_data_truth.py tests/test_v3_decision_policy.py -q`

## Next intended step
Observe production shadow logs for guardrail firing rate across at least one full portfolio cycle. Expected: guardrail fires for PRESENT/MEDIUM evidence cards (1-2 trusted dims). Confirm zero visible behavior drift. Then consider: (a) extending guardrail to enforce per-axis evidence minimums for more conviction bands, or (b) Snapshot Store for evidence-quality trend tracking over time.

---

## Last change
Intel v3 PR 8: optional INFO-level truth-aware v3 shadow suppression summary observability.

## Severity
Level 1 — small backend observability/control follow-up.

## Assumptions and success criteria
- Reuse the existing PR 4 env-gated INFO shadow summary pattern, with default behavior unchanged.
- Keep DEBUG summary behavior intact and emit only one portfolio-level summary per batch.
- Add only aggregate truth-aware suppression diagnostics to INFO payload (no raw/sensitive card or user/account/provider data).
- No visible recommendation/action/UI/API/Deploy/SQL/provider/LLM behavior changes.

## Fix
- Added `summarize_truth_aware_suppression()` in `v2/backend/app/services/intelligence/v3/shadow_projection.py`:
  - Aggregates `safe_axis_count`, `unsafe_axis_count`, `suppressed_axis_reasons` (reason-code counts), and `dominant_truth_reason` across projected cards.
  - Uses only aggregate values from per-card `truth_diagnostics` and remains pure/deterministic.
- Updated `v2/backend/app/services/recommendation_engine.py`:
  - Added `_build_v3_shadow_info_summary()` that merges PR 3/4 portfolio summary with PR 7 truth-aware suppression aggregates.
  - Reused existing PR 4 env flag `INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED` and existing logger helper.
  - Kept default disabled behavior; DEBUG log still emits as before, INFO emits only when env is explicitly truthy.
- Updated `v2/backend/tests/test_recommendation_engine.py`:
  - Extended INFO payload contract assertions with truth-aware aggregate keys.
  - Added focused test for stable truth-aware aggregate counts and dominant reason selection.

## Observability contract (PR 8)
- Env control reused: `INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED`.
- Default unchanged: no extra INFO summary when flag is unset/false.
- When enabled: exactly one INFO-level portfolio summary per recommendation card batch.
- INFO payload remains aggregate-only and now includes:
  - `unsafe_axis_count`
  - `suppressed_axis_reasons`
  - `safe_axis_count`
  - `dominant_truth_reason`
  - Existing PR 4/3 keys (`projected_cards`, `projection_failures`, `hold_collapse_risk_count`, `honest_hold_count`, etc.)
- Excludes raw card payloads, raw metric keys, advanced metric names, user/account identifiers/values, holdings quantities, provider payloads, and LLM text.

## Explicit non-changes
- No visible UI behavior change.
- No API schema change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No policy-gating added.

## Test results
- `pytest -q backend/tests/test_recommendation_engine.py backend/tests/test_v3_shadow_projection.py backend/tests/test_v3_truth_aware_adapter.py backend/tests/test_v3_data_truth.py backend/tests/test_v3_decision_policy.py`
- Result: 315 passed.

## Next intended step
Observe production/Railway truth-suppression rates under env-enabled INFO logs for at least one full portfolio cycle, then evaluate whether policy-gating (e.g., stricter BUY evidence requirement) should be proposed in a separate PR.

---


## Last change
Intel v3 PR 7: truth-aware v3 shadow input adapter.

## Severity
Level 2 — foundational backend integration. Wires PR 6 Data Truth Contract into v3 shadow decision path; backend-only dark launch.

## Root cause
The v3 decision kernel (PRs 1-5) built DecisionInputV3 from raw card/signal values without consulting the PR 6 Data Truth Contract. Weak, missing, stale, conflicting, or unavailable signals were treated as equally valid evidence. This risked HOLD-collapse suppression being too aggressive (treating MISSING as equivalent to PRESENT) or too passive (trusting conflicting signals).

## Fix
- Added `build_truth_aware_decision_input()` to `v2/backend/app/services/intelligence/v3/existing_signal_adapter.py`:
  - Calls `evaluate_card_signals_truth()` first to classify all signal axes.
  - Axes with `safe_for_decision=False` (MISSING, UNAVAILABLE, CONFLICTING, STALE) have their corresponding input signals nulled before `build_decision_input_from_card()` is called.
  - Axes with WEAK findings remain safe (PR 6 contract: `safe_for_decision=True` with LOW trust).
  - Axis→input mapping: evidence_quality → data_quality_label + intel_read; action_signal → action + analyst_action; conviction → conviction_level; technical_signal → technical_signal; risk_signal → risk_flag + analyst_risks.
  - Returns (DecisionInputV3, truth_summaries, suppressed_by_truth).
  - truth_suppressions annotated into DecisionInputV3.suppression_reasons as `truth_{axis}` keys.
- Modified `v2/backend/app/services/intelligence/v3/shadow_projection.py`:
  - `project_shadow_from_card_signals()` now uses `build_truth_aware_decision_input()` instead of `build_decision_input_from_card()` + separate `evaluate_card_signals_truth()`.
  - `truth_diagnostics` sub-dict extended with 5 new keys (additive — all PR 2/3 stable keys unchanged):
    - `truth_aware_adapter_enabled` — True when truth eval succeeds
    - `safe_axis_count` — count of safe axes
    - `unsafe_axis_count` — count of unsafe axes
    - `suppressed_axis_reasons` — axis_name → dominant_reason_code for each suppressed axis
    - `dominant_truth_reason` — most common suppression reason code, or "none"
- Added `v2/backend/tests/test_v3_truth_aware_adapter.py` — 68 new tests across 15 test classes.

## Truth-aware suppression rules (PR 7)
- `AxisTruthSummary.safe_for_decision=False` → null the axis inputs (suppresses only that axis).
- WEAK (safe_for_decision=True, LOW trust) → pass through unchanged.
- Suppression is per-axis; other axes remain independent and can still produce TRIM/SELL/BUY.
- Axis suppression annotated in suppression_reasons so honest-hold detection works.
- The outer fail-soft wrapper in project_shadow_from_card_signals is preserved; any exception → None.

## Dark-launch safety notes
- All PR 2/3/4/5/6 stable diagnostic keys unchanged.
- No visible v2 recommendation, action, or card behavior changed.
- No API schema, frontend, Deploy, SQL, provider, or LLM changes.
- v3 shadow action may change for cards with CONFLICTING/UNAVAILABLE/MISSING signals (correct and intended).

## Explicit non-changes
- No visible UI behavior change.
- No API schema change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No real user/account data in fixtures.
- No mutation of visible v2 card actions.
- No frontend/API/Deploy/schema files changed.

## Test results
- 68 new tests in `test_v3_truth_aware_adapter.py` — all pass.
- 85 existing `test_v3_data_truth.py` tests — all pass.
- 66 existing decision_policy + shadow_projection tests — all pass.
- Total: 219 tests pass.

## Next intended wiring step
Promote truth-aware shadow diagnostics to optional INFO-level logging (env-gated, following PR 4 pattern) so production shadow observability exposes the per-portfolio truth suppression summary. Keep dark-launch until at least one full production portfolio confirms zero behavior drift. Then consider policy-gating: only allow BUY when evidence_quality axis is PRESENT/HIGH (not just safe), to raise the bar for high-conviction buys.

---

## Last change
Intel v3 PR 6: backend-only Data Truth Contract v1 for existing Intel/recommendation signals.

## Severity
Level 2 — foundational backend contract layer. Adds durable v3 data-truth foundation; backend-only, scoped.

## Root cause
The v3 decision kernel (PRs 1-5) classifies signals into axis bands but cannot yet distinguish missing data from stale data, weak/inferred data from sourced/current data, or conflicting signals from aligned signals. Without a truth layer, the policy risks treating absent evidence as equivalent to present evidence, or pretending weak data is strong.

## Fix
- Added `v2/backend/app/services/intelligence/v3/data_truth_contracts.py`:
  - `DataTruthStatus` enum: PRESENT / MISSING / STALE / WEAK / CONFLICTING / UNAVAILABLE
  - `SourceTrustLevel` enum: HIGH / MEDIUM / LOW / UNKNOWN
  - `DataTruthFinding` dataclass: signal_name, status, trust_level, source_kind, freshness_label, reason_code, safe_for_decision
  - `AxisTruthSummary` dataclass: axis_name, findings, present_count, missing_count, stale_count, weak_count, safe_for_decision, dominant_reason_code
- Added `v2/backend/app/services/intelligence/v3/data_truth_v1.py` — pure evaluator:
  - `classify_evidence_signals(data_quality_label, intel_read)` — intel_read takes precedence; insufficient_data=True → WEAK; 0 trusted dims → WEAK; ≥3 → PRESENT/HIGH
  - `classify_action_signals(action, analyst_action)` — BUY↔SELL direct opposition → CONFLICTING; both valid → PRESENT/HIGH
  - `classify_conviction_signal(conviction_level)` — LOW → WEAK; HIGH/MEDIUM → PRESENT
  - `classify_technical_signal(technical_signal)` — known values → PRESENT/MEDIUM
  - `classify_risk_signals(risk_flag, analyst_risks)` — no data → MISSING; any text → PRESENT
  - `classify_with_staleness(signal_name, value, last_updated_hours_ago, stale_threshold_hours)` — future API for timestamp-aware staleness; STALE when age > threshold
  - Explicit provider-unavailable sentinels (UNAVAILABLE / N/A / UNAVAIL etc.) → UNAVAILABLE
- Added `v2/backend/app/services/intelligence/v3/existing_signal_truth_adapter.py`:
  - `evaluate_card_signals_truth(...)` → list[AxisTruthSummary], one per signal group axis
  - `build_truth_diagnostic_summary(summaries)` → compact dict with stable keys for shadow logging
  - `_build_axis_summary(axis_name, findings)` — safe_for_decision logic: any safe finding + no CONFLICTING/UNAVAILABLE
- Modified `v2/backend/app/services/intelligence/v3/shadow_projection.py`:
  - Added optional `truth_diagnostics` key to the shadow projection dict (additive — existing stable keys unchanged)
  - truth_diagnostics is None on failure (fail-soft wrapper)
- Added `v2/backend/tests/test_v3_data_truth.py` — 85 new tests across 12 test classes.

## Truth classification rules (v1)
- None / empty → MISSING, safe_for_decision=False
- Provider sentinel strings → UNAVAILABLE, safe_for_decision=False
- intel_read.insufficient_data=True or 0 trusted dims → WEAK, safe_for_decision=True (LOW trust)
- data_quality_label=LOW or conviction=LOW → WEAK, safe_for_decision=True (LOW trust)
- BUY↔SELL direct action opposition → CONFLICTING, safe_for_decision=False
- Otherwise valid present values → PRESENT, safe_for_decision=True
- STALE: requires caller-supplied age data; safe_for_decision=False when age > threshold

## Explicit non-changes
- No visible UI behavior change.
- No API schema change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No real user/account data in fixtures.
- No v3 action decision mutation (truth is diagnostic-only for this PR).
- No frontend/API/Deploy/schema files changed.

## Test results
- 85 new tests in `test_v3_data_truth.py` — all pass.
- 66 existing tests (decision_policy + shadow_projection) — all pass.
- Total: 151 tests pass.

## Next intended wiring step
Wire `evaluate_card_signals_truth` output into `DecisionInputV3` so the v3 policy can inspect axis-level safe_for_decision before acting (e.g., suppress BUY on axes where all data is MISSING or WEAK). Keep as diagnostic until at least one PR validates zero behavior drift in shadow observability.

## Last change
Intel v3 PR 5: backend v3 shadow golden-portfolio validation suite (PR: "test(intel-v3-pr5): add synthetic golden-portfolio shadow validation suite").

## Severity
Level 1 — focused validation/test hardening.

## Root cause
PRs 2-4 established v3 shadow projection + portfolio summary behavior, but there was no single realistic held-portfolio regression suite proving action diversity, HOLD-collapse detectability, honest HOLD separation, deterministic counts, and fail-soft handling together.

## Fix
- Extended `v2/backend/tests/test_v3_shadow_projection.py` with `TestV3ShadowGoldenPortfolio` synthetic fixture suite.
- Reused existing v3 helpers only:
  - `project_shadow_from_card_signals(...)`
  - `summarize_shadow_diagnostics(...)`
- Added realistic synthetic held-card scenarios in one deterministic fixture set:
  - strong BUY-like signals while visible action is HOLD,
  - TRIM-like overextended risk,
  - SELL-like protection/risk,
  - true neutral HOLD,
  - honest HOLD from insufficient data,
  - malformed/partial card that fail-softs safely.
- Added focused assertions that:
  - v3 shadow produces action diversity (BUY/HOLD/TRIM/SELL),
  - HOLD-collapse risk is detected without mutating visible v2 actions,
  - honest insufficient-data HOLD is counted separately,
  - projection failures are counted safely and deterministically,
  - portfolio summary schema/counts remain stable.

## Explicit non-changes
- No visible UI behavior change.
- No API schema change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No real user/account data used in fixtures.


## Last change
Intel v3 PR 4: production-visible backend shadow observability control (PR: "feat(intel-v3-pr4): optional info-level v3 shadow portfolio summary logging").

## Severity
Level 1 — small backend observability/control follow-up.

## Root cause
PR 3 emitted the portfolio-level v3 shadow summary at DEBUG only. In production environments where DEBUG is suppressed, the summary was hard to validate live even though the payload is safe aggregate diagnostics.

## Fix
- Added backend-only env-gated info logging control in `recommendation_engine.py`:
  - `INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED` (default disabled)
  - `_is_v3_shadow_summary_info_logs_enabled()` for deterministic env parsing
  - `_log_v3_shadow_projection_portfolio_summary(...)` helper that always logs DEBUG and conditionally logs INFO
- Kept summary schema identical for DEBUG and INFO: uses existing `summarize_shadow_diagnostics(...)` aggregate output only.
- Wired existing PR 3 summary emission through the new helper so one summary is emitted per card assembly batch, not per card.
- Added focused tests in `test_recommendation_engine.py` for:
  - default disabled behavior (no INFO summary),
  - enabled behavior (single INFO summary),
  - stable/safe aggregate key contract in payload.

## Observability contract (PR 4)
- DEBUG summary remains unchanged and always emitted.
- INFO summary is emitted only when `INTEL_V3_SHADOW_SUMMARY_INFO_LOGS_ENABLED` is truthy (`1/true/yes/on`, case-insensitive).
- INFO summary contains aggregate non-sensitive keys only:
  - `schema_version`
  - `total_cards`
  - `projected_cards`
  - `projection_failures`
  - `v2_visible_action_counts`
  - `v3_shadow_action_counts`
  - `hold_collapse_risk_count`
  - `honest_hold_count`
  - `non_hold_shadow_from_v2_hold_count`
- No raw card payloads, raw metric keys, account values, user identifiers, provider payloads, or LLM internals are added to INFO payload.

## Explicit non-changes
- No visible UI behavior change.
- No API schema change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.
- No recommendation/action mutation.

## Last change
Intel v3 PR 2: Safe backend v3 shadow projection/logging (PR: "feat(intel-v3-pr2): backend v3 shadow projection and HOLD-collapse diagnostics").

## Root cause
The v3 decision kernel (PR 1) was not wired to any runtime path — it existed as a callable helper (`_v3_shadow_decide`) but produced no observability during real card assembly. Without wiring, HOLD-collapse risk could not be measured against actual existing InsightCard data.

## Fix
- Added `v2/backend/app/services/intelligence/v3/shadow_projection.py` — pure function `project_shadow_from_card_signals()` that takes InsightCard signal fields, runs the v3 adapter + policy, and returns a diagnostic dict with stable keys. No IO, no supabase dependency.
- Added `_v3_shadow_projection(card: InsightCard) → Optional[dict]` in `recommendation_engine.py` — wraps `_v3_shadow_decide()` and builds the diagnostic dict from `card.action` and the v3 output. Fail-soft (returns None on any error).
- Wired `_v3_shadow_projection(card)` after `cards.append(card)` in `_compute_insight_cards`. Shadow result logged at DEBUG with stable key names. Never blocks card assembly, never modifies card, never raises.
- Added `v2/backend/tests/test_v3_shadow_projection.py` — 28 table-driven tests across 8 test classes covering all acceptance criteria.

## Shadow projection diagnostic contract (stable keys)
```
ticker              — ticker symbol
v2_visible_action   — post-gate visible v2 action (BUY/HOLD/TRIM/SELL)
v3_shadow_action    — v3 policy output action
v3_shadow_conviction — v3 policy output conviction
hold_collapse_risk  — True when v2==HOLD but v3 says BUY/TRIM/SELL
v3_honest_hold      — True when v3==HOLD due to thin/suppressed evidence
suppressed_axes     — per-axis suppression reasons list
v3_schema_version   — schema version string
```

## Dark-launch safety notes
- `_v3_shadow_projection` runs inside the existing try/except that wraps card assembly — any failure is caught, logged at DEBUG, and returns None.
- Shadow result is logged at DEBUG only; not exposed to any API route, frontend, or Deploy path.
- `card.action` (post-gate visible action) is passed as `v2_visible_action` — the v3 shadow may diverge from it using other signals (e.g., `analyst_action=BUY` when card gated to HOLD under `insufficient_data`). This divergence is the key HOLD-collapse diagnostic.
- `project_shadow_from_card_signals` in the v3 module is importable without supabase — safe for tests.

## Explicit non-changes
- No visible UI behavior change.
- No Deploy changes.
- No SQL / Supabase migrations.
- No frontend changes.
- No new providers (EDGAR/FRED/Finnhub/yfinance untouched).
- No allocation math changes.
- No LLM calls.
- No API response schema changes.

## Test results
- 28 new tests in `test_v3_shadow_projection.py` — all pass.
- 27 existing `test_v3_decision_policy.py` tests — all pass.
- Total: 55 tests pass.

## Next PR recommendation
Wire a portfolio-level HOLD-collapse report using the shadow diagnostic across all assembled cards (count `hold_collapse_risk` cards, log portfolio-level summary). OR implement the v3 fit-band portfolio governor. Do not begin until this PR is merged.

## Last change
Intel v3 PR 1: Minimal backend v3 decision kernel — dark launch (PR: "feat(intel-v3-pr1): minimal backend v3 decision kernel dark launch").

## Root cause
Intel v2 posture derivation applies a global insufficient-data gate that collapses many tickers to HOLD/LOW even when per-axis suppression would be more precise. No deterministic BUY/HOLD/TRIM/SELL policy exists that is independent from the v2 posture path.

## Fix
- Created `v2/backend/app/services/intelligence/v3/` package (dark launch, backend-only).
- `decision_contracts.py` — typed enums (ActionV3, ConvictionV3, AxisBand, PriceBand, FitBand, RiskBand) and dataclasses (DecisionInputV3, DecisionOutputV3). Schema version v3.1.
- `decision_policy_v1.py` — deterministic policy in priority order: SELL → TRIM → BUY → HOLD. Uses five independent axes. No composite score. No LLM action labels. Conviction caps: THIN→LOW, price SUPPRESSED→MEDIUM max, risk HIGH/CRITICAL+BUY→LOW.
- `existing_signal_adapter.py` — builds DecisionInputV3 from existing InsightCard fields (action, analyst_action, conviction_level, technical_signal, risk_flag, analyst_risks, category, data_quality_label, intel_read, thesis_v2). Per-axis suppression when signal missing. No new providers.
- `v2/backend/tests/test_v3_decision_policy.py` — 20 table-driven tests covering all 9 acceptance criteria: BUY on strong evidence, HOLD+LOW on thin evidence, TRIM on overweight/breach, SELL on critical risk, price-suppressed capping, mixed-fixture differentiation, legacy label safety, no raw metric keys in rationale, no BUY when THIN.
- `recommendation_engine.py` — added `_v3_shadow_decide(card)` helper at module end (dark launch). Not wired to any route or UI surface. Callable from internal logging or tests.

## Explicit non-changes
- No Deploy changes.
- No SQL / Supabase migrations.
- No visible frontend behavior change.
- No new providers (EDGAR/FRED/Finnhub/yfinance untouched).
- No allocation math changes.
- No Data Truth, Snapshot Store, Opportunity Radar, or LLM Committee.

## Next PR recommendation
Wire a safe shadow projection from the existing `_compute_insight_cards` path (log v3 decision alongside v2 posture without surfacing it), OR implement a portfolio governor that uses v3 fit bands to validate rebalancing targets. Do not begin next PR until this PR is merged.

## Last change
Lock Intel v3 visible action contract and add frontend regression canaries (PR: "fix(intel-v3-pr0): lock visible action contract + hold-collapse canary").

## Root cause
Intel UI regressions repeatedly reintroduced legacy posture labels and a prior failure mode where meaningful recommendation mixes collapsed into all HOLD/LOW in visible output. The display contract needed an explicit lock plus synthetic regression guards.

## Fix
- `recommendations/page.tsx`: locked visible tabs to `ALL/BUY/HOLD/TRIM/SELL`, added contract comment, and centralized filter normalization through a shared helper.
- `AgentInsightCard.tsx`: badge action now always uses shared visible-action normalization; unknown/legacy labels normalize to HOLD (no UI crash, no legacy label exposure).
- Added `visibleIntelActions.ts` helper to enforce the only visible held actions (`BUY/HOLD/TRIM/SELL`) and default unknown labels to HOLD safely.
- Added focused frontend tests (`visibleIntelActions.test.ts`) that fail if forbidden posture labels appear or if a synthetic non-degenerate recommendation mix collapses into all HOLD/LOW.

## Explicit non-changes
- No backend logic changes.
- No Deploy changes.
- No SQL/schema changes.
- No v3 architecture/module scaffolding.


## Last change
Simplify Intel UI to single action model — BUY/HOLD/TRIM/SELL replacing posture bucket UI (PR: "fix(intel): simplify Intel display contract — BUY/HOLD/TRIM/SELL everywhere").

## Root cause
Two competing recommendation systems in the Intel UI: Portfolio Command Center (PortfolioSynthesisPanel) already used BUY/HOLD/TRIM/SELL, but the filter tabs and card badges used posture buckets (Add Candidate, Watchlist, Review, Risk Watch, Trim Candidate) introduced in earlier PRs. Multiple patch attempts failed to resolve the inconsistency. Decision: abandon the posture bucket UI contract as the primary visible system; return to one simple action model everywhere.

## Fix
- `page.tsx`: Replaced `INTEL_FILTERS` posture buckets with `ALL/BUY/HOLD/TRIM/SELL`. Added `normalizeDisplayAction` helper. Filter counts and filtering now use `normalizeDisplayAction(r.analyst_action || r.action)` — no longer reads `intel_filter_bucket`.
- `AgentInsightCard.tsx`: Removed `POSTURE_STYLES`, removed `intel_posture_label` from card badge logic. Badge always shows normalized action (BUY/HOLD/TRIM/SELL). `normalizeAction` now maps REVIEW → HOLD for full alignment. Removed `actionBadgeLabel` (no "WATCHLIST" relabeling).
- `AgentInsightCardThesisVisibility.test.tsx`: Replaced posture-contract section 10 with new section testing BUY/HOLD/TRIM/SELL unified display contract. Added section 11 for Evidence check label contract.

## Explicit non-changes
- No backend changes. `intel_posture_label` and `intel_filter_bucket` fields remain in the data model for backend compatibility — just not consumed by the visible UI.
- No Deploy/allocation changes.
- No SQL.
- No Business Read re-enable.
- No new posture/category system.
- `PortfolioSynthesisPanel` already used BUY/HOLD/TRIM/SELL — no changes needed there.

## Intel display contract (active)
- Visible filter buckets: ALL / BUY / HOLD / TRIM / SELL
- Card badge: normalized action (BUY/HOLD/TRIM/SELL), never posture label
- Evidence check section: secondary context only (Reliable/Missing chips + posture_reason text)
- WHY/RISK/ACTION/ALT VIEW: ticker-specific, remain unchanged
- Business Read: hidden
- Raw metric keys: not rendered

## Files changed
- `v2/frontend/src/app/dashboard/recommendations/page.tsx`
- `v2/frontend/src/components/cards/AgentInsightCard.tsx`
- `v2/frontend/src/components/cards/AgentInsightCardThesisVisibility.test.tsx`
- `docs/ai/HANDOFF.md`
- `v2/progress_log.md`

## Last change
Fix Intel posture page-load vs post-run inconsistency — all 34 cards landing in Watchlist on initial page load but correctly distributed after Run Agents (PR: "fix(intel): fix page-load posture collapse — pre-gate signals + fallback provenance").

## Root cause
Two compounding bugs caused all 34 cards to show Watchlist on page load while showing Add=13/Watchlist=14/Risk Watch=7 after Run Agents:

### A. Post-gate action fed into posture derivation
`_compute_insight_cards` applied the display safety gate (BUY→HOLD under `insufficient_data`) before calling `_derive_intel_posture`. On page load, old recommendations have `action=BUY` but the fallback `_reasoning_v2` (from a prior degraded run) had `insufficient_data=True`, so the gate fired and set `_card_action="HOLD"`. `_derive_intel_posture(HOLD, LOW, insufficient=True)` → Rule 8: Watchlist. After Run Agents, the new primary run had `insufficient_data=False`, the gate did not fire, `_card_action` stayed "BUY", and Rule 5 fired: Add Candidate.

Fix: saved pre-gate values (`_pre_gate_action`, `_pre_gate_analyst_action`, `_pre_gate_conviction_level`) and passed those to `_derive_intel_posture` instead of the post-gate values.

### B. Fallback run's `insufficient_data` flag gating posture for a different run's data
When `intel_read` came from the FALLBACK run (not the recommendation's own `agent_run_id`), its `insufficient_data=True` reflected a different run's data quality — not the card's actual agent assessment. This caused the posture derivation to treat a genuine BUY ticker as having thin data.

Fix: `_build_intel_read_for_card` now returns `(intel_read_dict, is_from_primary_run: bool)`. When `is_from_primary=False`, `_intel_read_for_posture` is set to `None`, so `insufficient=False` and the pre-gate BUY fires Rule 5: Add Candidate.

### C. Rule 5.5 — BUY + own run's insufficient_data → Review
Edge case: when the card's OWN run says `insufficient_data=True` but the agent still assessed BUY, posture now returns "Review" (not "Watchlist"). Separates "agent sees upside but coverage thin" from "no constructive signal at all."

## Scenario trace after fix
| Scenario | is_from_primary | pre-gate action | insufficient | Posture |
|---|---|---|---|---|
| Page load: old BUY + fallback `insufficient_data=True` | False | BUY | False (ignored) | Add Candidate ✓ |
| Page load: HOLD+LOW + no `_reasoning_v2` | False | HOLD | False | Watchlist ✓ |
| Post-run: BUY + primary `insufficient_data=False` | True | BUY | False | Add Candidate ✓ |
| Post-run: BUY + primary `insufficient_data=True` (Rule 5.5) | True | BUY | True | Review ✓ |
| Risk Watch 7: bearish tech signal in DB | — | — | — | Risk Watch ✓ (Rule 4) |

## Explicit non-changes
- No Deploy changes.
- No allocation math changes.
- No score threshold changes.
- No Supabase SQL/migrations.
- No LLM calls or model behavior changes.
- No Business Read re-enable.
- No new intel modules.
- No raw metric keys exposed.
- No frontend changes.

## Files changed
- `v2/backend/app/services/recommendation_engine.py` — `_build_intel_read_for_card` returns `(dict, bool)` provenance tuple; `_compute_insight_cards` saves pre-gate signals, uses `_intel_read_for_posture = intel_read if is_from_primary else None`; `_derive_intel_posture` Rule 5.5 added
- `v2/backend/tests/test_intel_read_projection.py` — added `_build_intel_read_for_card_with_provenance` helper; Rule 5.5 added to local `_derive_intel_posture`; 5 new regression tests (36–40)
- `docs/ai/HANDOFF.md` — this entry

## Acceptance criteria
1. Page-load BUY tickers → Add Candidate (fallback `insufficient_data` no longer gates posture).
2. Page-load HOLD/LOW tickers → Watchlist (no spurious upgrade).
3. Post-run BUY + sufficient primary data → Add Candidate (Rule 5).
4. BUY + primary `insufficient_data=True` → Review (Rule 5.5, not Watchlist).
5. Risk Watch 7 tickers unchanged (Rule 4 fires before any of the above).
6. 61 backend tests pass.

---

## Last change
Build end-to-end Intel advisor posture contract — replace broker-style filter collapse with advisor posture buckets (PR: "feat(intel): add intel posture system — advisor posture buckets replacing broker HOLD collapse").

## Root cause
All 34 tickers had `action=HOLD` after the insufficient_data gate blocked BUY. The filter tabs were keyed to `r.action` (broker-style BUY/HOLD/SELL/TRIM/REVIEW), so HOLD=34/BUY=0 was the permanent display state. Card badges all showed "WATCHLIST" (HOLD + insufficient_data relabel) with no differentiation between ETFs, crypto, speculative positions, MEDIUM-conviction single stocks, or tickers with TRIM signals.

## New Intel posture contract (v3)

### Posture derivation (`_derive_intel_posture`)
Deterministic function in `recommendation_engine.py`. Evaluated in priority order:
1. TRIM/SELL action → **Trim Candidate**
2. Core index / dividend ETFs (VOO, VTI, QQQ, SCHD, etc.) → **Add Candidate** (DCA target regardless of data coverage)
3. Crypto / speculative tickers (BTC, XRP, RIVN, KLAR, BLSH, STUB) → **Risk Watch**
4. Bearish technical signal (SELL/WEAK/BEARISH) → **Risk Watch**
5. BUY action + sufficient data → **Add Candidate**
6. MEDIUM+ conviction + sufficient data → **Add Candidate**
7. Insufficient data + MEDIUM conviction → **Review** (partial evidence, worth watching)
8. Everything else → **Watchlist**

### New InsightCard fields
- `intel_posture_label: Optional[str]` — advisor badge shown on card
- `intel_filter_bucket: Optional[str]` — key for filter tab counting + filtering

### posture_reason (card-specific "Why this view?")
`build_posture_reason()` in `reasoning_v2_plain_english.py` generates a card-specific sentence explaining WHY this posture was assigned. Injected into `intel_read_dict["posture_reason"]` during card assembly. `WhyThisView` component shows `posture_reason` as primary text over `bottom_line`/`summary`.

### Filter tabs
`INTEL_FILTERS` replaces `ACTION_FILTERS` in `recommendations/page.tsx`:
- All | Add Candidate | Watchlist | Review | Risk Watch | Trim Candidate
Counts use `r.intel_filter_bucket` (not `r.action`). Legacy cards without the field default to Watchlist bucket.

### Card badge
`AgentInsightCard.tsx` uses `card.intel_posture_label` for the badge. `POSTURE_STYLES` map provides color coding (green=Add, blue=Watchlist, purple=Review, red=Risk Watch, yellow=Trim). Card border also reflects posture.

## Explicit non-changes
- No Deploy changes.
- No allocation math changes.
- No score threshold changes.
- No Supabase SQL/migrations.
- No LLM calls or model behavior changes.
- No Business Read re-enable.
- No new data providers.
- No raw metric keys exposed.

## Files changed
- `v2/backend/app/models/recommendation.py` — added `intel_posture_label`, `intel_filter_bucket` to InsightCard
- `v2/backend/app/services/intelligence/reasoning_v2_plain_english.py` — added `build_posture_reason()`, `_INTEL_RISK_WATCH_TICKERS` set
- `v2/backend/app/services/recommendation_engine.py` — added `_derive_intel_posture()`, `_INTEL_ADD_CANDIDATE_TICKERS`, `_INTEL_RISK_WATCH_TICKERS`; wired posture computation + posture_reason injection into `_compute_insight_cards`; updated import
- `v2/backend/tests/test_intel_read_projection.py` — local `_derive_intel_posture` copy for testing; 13 new posture tests (23-35)
- `v2/frontend/src/lib/api.ts` — added `intel_posture_label`, `intel_filter_bucket` to InsightCardData; added `posture_reason` to IntelRead
- `v2/frontend/src/components/cards/AgentInsightCard.tsx` — added `POSTURE_STYLES`; badge uses `intel_posture_label`; `WhyThisView` prefers `posture_reason`
- `v2/frontend/src/app/dashboard/recommendations/page.tsx` — replaced `ACTION_FILTERS` with `INTEL_FILTERS`; filter counts use `intel_filter_bucket`
- `v2/frontend/src/components/cards/AgentInsightCardThesisVisibility.test.tsx` — 9 new frontend posture tests (group 10)
- `v2/progress_log.md` — this entry
- `docs/ai/HANDOFF.md` — this entry

## Acceptance criteria met
1. ETF tickers (VOO, VTI, SCHD, etc.) → Add Candidate bucket, not HOLD/Watchlist.
2. Crypto/speculative (BTC, XRP, RIVN, KLAR) → Risk Watch bucket.
3. MEDIUM conviction insufficient-data tickers → Review bucket.
4. LOW conviction insufficient-data tickers → Watchlist bucket.
5. TRIM/SELL action → Trim Candidate bucket (highest priority).
6. Filter tabs show Intel posture buckets, not BUY=0/HOLD=34 collapse.
7. Card badge shows intel_posture_label (e.g., "Review"), not relabeled "WATCHLIST" for all.
8. "Why this view?" posture_reason is card-specific — different postures produce different text.
9. No raw metric keys in any output.
10. Business Read remains hidden.
11. BUY/HIGH still blocked under insufficient_data.
12. No SQL migration required.

---

## Last change
Fix Intel page-load vs Run Agents inconsistency + over-downgrade where all tickers become HOLD (PR: "fix(intel): fix page-load WHY inconsistency and differentiate conviction under insufficient-data").

## Root cause

### A. Page-load vs Run Agents WHY inconsistency
`_compute_insight_cards` prefers `latest_live_llm_by_ticker` over the recommendation's linked `analyst_row` only when `analyst_row is None or used_fallback=True`. Recommendations written before Phase-7 memo fields (`primary_driver`, `conviction_level`) were introduced have `used_fallback=False` but no `primary_driver` — so the fresh row from the latest completed run was silently skipped. This meant page load produced `reasoning["primary_driver"]=None`, which the `insufficient_data` gate then replaced with generic `conservative_why`. After clicking Run Agents, a new run writes fresh analyst rows with `primary_driver`, so the fresh data was visible only within the same session — not on the next page load.

Fix: added `_lacks_memo_fields` check — if the current `analyst_verdict` has no `primary_driver`, also prefer `latest_live_llm_by_ticker` (the freshest non-fallback `human_v2`/`compact_v1` row).

### B. All-HOLD over-downgrade
The `insufficient_data` gate blanket-downgraded `HIGH → LOW` conviction regardless of how many trusted signals existed. With all tickers having `insufficient_data=True` (growth/risk suppressed) and the same `conviction_level=LOW`, all 34 cards were functionally identical.

Fix: replaced the single `HIGH → LOW` rule with a conservative conviction ladder based on `n_trusted = len(trusted_signals)`:
- HIGH + ≥3 trusted signals → **MEDIUM** (meaningful partial evidence warrants some conviction)
- HIGH + <3 trusted signals → **LOW** (too sparse for any conviction)
- MEDIUM + <2 trusted signals → **LOW** (very weak coverage)
- MEDIUM + ≥2 trusted signals → **preserved** (adequate partial evidence)
- LOW → **preserved** always

BUY → HOLD remains unconditional. HIGH conviction is always blocked. Differentiation comes from conviction level and ticker-specific WHY/RISK/ALT VIEW, not from action.

## Page-load vs Run Agents consistency contract (v4)
- Page load must produce the same `primary_driver` as Run Agents for the same ticker when a valid non-fallback `human_v2`/`compact_v1` analyst row exists in `agent_insights`.
- `latest_live_llm_by_ticker` is the tiebreaker: used when `analyst_row is None`, `used_fallback=True`, **or** `primary_driver` is absent from the current verdict.
- Ticker-specific WHY is preserved on page load when the freshest analyst verdict has a safe `primary_driver`.
- Fallback to `conservative_why` only when no analyst row has `primary_driver` for this ticker.

## Conservative posture ladder / insufficient-data policy (v2)
- BUY is always blocked under `insufficient_data`. Action remains HOLD.
- HIGH conviction is always blocked: downgraded to MEDIUM or LOW based on `n_trusted`.
- MEDIUM conviction is preserved when ≥2 trusted signals exist; downgraded to LOW otherwise.
- LOW conviction is always preserved.
- Ticker-specific WHY/RISK/ALT VIEW preserved when safe (no forbidden bullish phrases).
- ACTION always replaced with `conservative_action`.
- Cards remain differentiated by conviction level even when all share action=HOLD.

## Files changed
- `v2/backend/app/services/recommendation_engine.py` — `_lacks_memo_fields` analyst_row preference condition; conviction ladder replacing blanket HIGH→LOW
- `v2/backend/tests/test_intel_read_projection.py` — updated test 13 inline simulation; added `_simulate_conviction_ladder` helper; added tests 18-22 (conviction ladder cases + page-load preference simulation)
- `v2/progress_log.md` — this entry
- `docs/ai/HANDOFF.md` — this entry

## Acceptance criteria met
1. Page load uses freshest available `primary_driver` when analyst_verdict lacks it (Issue A).
2. Not all 34 tickers share identical conviction_level=LOW under insufficient_data (Issue B).
3. BUY/HIGH remain blocked; no card shows BUY under insufficient_data.
4. Ticker-specific WHY/RISK/ALT VIEW preserved when safe.
5. All 9 forbidden bullish phrases still blocked.
6. Business Read remains hidden.
7. No raw metric keys in output.

## Explicit non-changes
- No Deploy changes.
- No allocation math changes.
- No score threshold changes.
- No Supabase SQL/migrations.
- No LLM calls or model behavior changes.
- No Business Read re-enable.
- No new data providers.
- No frontend component changes.

---

## Last change
Restore ticker-specific Intel card WHY while preserving insufficient-data safety (PR: "feat(intel-card): restore ticker-specific WHY on insufficient-data cards via sanitizer").

## Root cause
After the conservative consistency PR, the `insufficient_data` gate in `_compute_insight_cards` replaced `primary_driver` (WHY) and `differentiation` (ALT VIEW) unconditionally with generic signal-list copy (`conservative_why`) and `None`. This made NVDA, MSFT, TSM, META, KLAR all show identical WHY text ("Evidence on {trusted} is present, but {incomplete} are still incomplete — watchlist read only.") regardless of the available ticker-specific LLM analyst reasoning.

## Fix

### insufficient_data copy contract (v3)

| Card field | Source | Contract |
|---|---|---|
| WHY (`primary_driver`) | safe original OR `conservative_why` fallback | Ticker-specific analyst text when safe (no forbidden bullish phrases); `conservative_why` when original is absent or unsafe |
| ACTION (`action_reason`) | `conservative_action` | Always replaced — never ticker-specific under `insufficient_data` |
| RISK (`risk_flag`) | original | Always preserved — ticker-specific risk unchanged |
| ALT VIEW (`differentiation`) | safe original OR `None` | Preserved when safe (no forbidden bullish phrases); nulled when unsafe |
| WHY THIS VIEW main text | `bottom_line` | "Interesting setup, but {incomplete} are still missing — not enough complete evidence for a confident position." |
| Chips — Reliable | `trusted_signals` | Labeled "Reliable:" prefix |
| Chips — Missing | `incomplete_signals` | Labeled "Missing:" prefix |

### Forbidden bullish phrases (never rendered under insufficient_data)
`accumulate` · `buy` · `entry opportunity` · `re-rating opportunity` · `high-conviction idea` · `add aggressively` · `strong buy` · `deploy`

### Changes
1. `reasoning_v2_plain_english.py`: added `_FORBIDDEN_BULLISH_PHRASES` frozenset and `is_safe_for_insufficient_data(text)` pure function.
2. `recommendation_engine.py`: updated `insufficient_data` gate import and gate logic — ACTION always replaced; WHY/ALT VIEW preserved when safe, conservative fallback when absent/unsafe.

## Files changed
- `v2/backend/app/services/intelligence/reasoning_v2_plain_english.py` — added `_FORBIDDEN_BULLISH_PHRASES` + `is_safe_for_insufficient_data`
- `v2/backend/app/services/recommendation_engine.py` — import `is_safe_for_insufficient_data`; updated gate logic for WHY/ALT VIEW preservation
- `v2/backend/tests/test_intel_read_projection.py` — updated `_simulate_card_assembly_body_override`; 9 new tests for safe/unsafe preserve/replace behavior
- `v2/backend/tests/test_reasoning_v2_plain_english.py` — updated import; 13 new tests for `is_safe_for_insufficient_data`
- `v2/frontend/src/components/cards/AgentInsightCardThesisVisibility.test.tsx` — 4 new tests in group 9 (ticker-specific WHY preservation)
- `v2/progress_log.md` — this entry
- `docs/ai/HANDOFF.md` — this entry

## Acceptance criteria met
1. Safe ticker-specific WHY is preserved — NVDA/MSFT/TSM show different WHY text.
2. ACTION is always conservative under `insufficient_data`.
3. RISK (`risk_flag`) preserved unchanged.
4. Safe ALT VIEW preserved; unsafe ALT VIEW nulled.
5. WHY THIS VIEW (intel_read) remains deterministic coverage explanation with Reliable/Missing chips.
6. No generic identical WHY across cards when safe ticker-specific text exists.
7. No forbidden bullish phrases rendered under `insufficient_data`.
8. No raw metric keys in any output.
9. Missing intel_read degrades gracefully.
10. Business Read remains hidden.

## Explicit non-changes
- No Deploy changes.
- No allocation math changes.
- No score threshold changes.
- No Supabase SQL/migrations.
- No LLM calls or model behavior changes.
- No Business Read re-enable.
- No new data providers.

---

## Last change
Intel card insufficient-data copy: plain-English usefulness pass (PR: "feat(intel-card): make insufficient-data card copy specific and non-redundant").

## Root cause
After the conservative consistency PR, insufficient-data cards were safe but generic. `_build_conservative_why` (WHY) and `intel_read.summary` (WHY THIS VIEW) both produced nearly identical 3-sentence text about trusted/incomplete signals. `conservative_action` was passive ("Wait for X to improve. Keep on watchlist."). The frontend showed unlabeled chip groups with no label distinguishing trusted from incomplete.

## Fix

### insufficient_data copy contract (v2)

| Card field | Source | Contract |
|---|---|---|
| WHY (`primary_driver`) | `conservative_why` | 1 concise sentence: "Evidence on {trusted} is present, but {incomplete} are still incomplete — watchlist read only." |
| ACTION (`action_reason`) | `conservative_action` | "Stay on watchlist. Recheck after {incomplete} evidence improves or a new agent run fills those gaps." |
| WHY THIS VIEW main text | `bottom_line` (new) | "Interesting setup, but {incomplete} are still missing — not enough complete evidence for a confident position." |
| Chips — Reliable | `trusted_signals` | Labeled "Reliable:" prefix |
| Chips — Missing | `incomplete_signals` | Labeled "Missing:" prefix |

### Changes
1. `_build_conservative_why`: shortened to 1-sentence analyst note; distinct from `intel_read.summary`.
2. `_build_conservative_action`: changed to "Stay on watchlist. Recheck after..." pattern.
3. Added `_build_bottom_line`: WHY THIS VIEW conclusion sentence (different from WHY); only for insufficient_data.
4. `build_intel_read` now emits `bottom_line: str | None` (None when `insufficient_data=False`).
5. Frontend `IntelRead` type: added optional `bottom_line` field.
6. `WhyThisView`: shows `bottom_line || summary`; splits chip groups with "Reliable:" and "Missing:" labels.

## Files changed
- `v2/backend/app/services/intelligence/reasoning_v2_plain_english.py` — improved `_build_conservative_why`, `_build_conservative_action`; added `_build_bottom_line`; added `bottom_line` to output
- `v2/frontend/src/lib/api.ts` — added `bottom_line?: string | null` to `IntelRead`
- `v2/frontend/src/components/cards/AgentInsightCard.tsx` — updated `WhyThisView` to use `bottom_line || summary` + labeled chip groups
- `v2/backend/tests/test_reasoning_v2_plain_english.py` — updated schema keys test; 8 new tests for `bottom_line`, non-redundancy, conciseness, and action copy
- `v2/frontend/src/components/cards/AgentInsightCardThesisVisibility.test.tsx` — updated `collectIntelReadLines` for `bottom_line`; 2 new test groups
- `v2/progress_log.md` — this entry
- `docs/ai/HANDOFF.md` — this entry

## Acceptance criteria met
1. WHY and WHY THIS VIEW are not redundant — different sentences for same signals.
2. `conservative_action` leads with watchlist language and names gaps specifically.
3. `bottom_line` is WHY THIS VIEW conclusion; distinct from WHY.
4. Chip groups have "Reliable:" and "Missing:" labels for scannability.
5. No forbidden bullish phrases in any insufficient-data field.
6. No raw metric keys in output.
7. Business Read remains hidden.
8. Missing intel_read degrades gracefully (no change to null-safe path).

## Explicit non-changes
- No Deploy changes.
- No allocation math changes.
- No score threshold changes.
- No Supabase SQL/migrations.
- No LLM calls or model behavior changes.
- No Business Read re-enable.
- No new data providers.

---

## Last change
Intel card insufficient-data copy consistency (PR: "fix(intel-card): replace bullish body copy on insufficient-data cards with conservative watchlist language").

## Root cause
Previous PR fixed badge contradiction (BUY→HOLD, HIGH→LOW) but left the card body copy unchanged.
The fields `action_reason` (ACTION), `primary_driver` (WHY), and `differentiation` (ALT VIEW) still
carried legacy LLM text with bullish phrases ("Accumulate on pullbacks", "forward PE signals opportunity",
"high-conviction idea") even when `intel_read.insufficient_data=True`. Additionally `_build_summary`
used the phrase "high-conviction idea" in its WATCH+partial-coverage template.

**Fix**:
1. `build_intel_read` now emits two new backend-only fields (`conservative_action`, `conservative_why`)
   that are signal-specific, plain-English watchlist copy (only populated when `insufficient_data=True`).
2. Card assembly (`_compute_insight_cards`): when `insufficient_data=True`, also overrides
   `reasoning["action_reason"]` with `conservative_action`, `reasoning["primary_driver"]` with
   `conservative_why`, and nulls `reasoning["differentiation"]`.
3. `_build_summary` WATCH+partial-coverage path no longer uses the phrase "high-conviction idea";
   replaced with "not complete enough for a strong view".

## Files changed
- `v2/backend/app/services/intelligence/reasoning_v2_plain_english.py` — add `_build_conservative_action`,
  `_build_conservative_why` helpers; add `conservative_action`/`conservative_why` to output dict;
  fix forbidden phrase in `_build_summary`
- `v2/backend/app/services/recommendation_engine.py` — extend `insufficient_data` gate to also
  override `action_reason`, `primary_driver`, `differentiation` in `reasoning` dict
- `v2/backend/tests/test_reasoning_v2_plain_english.py` — 8 new tests for new fields and forbidden phrases;
  update schema-keys test for new required keys
- `v2/backend/tests/test_intel_read_projection.py` — 4 new tests for body-copy override in card assembly
- `v2/frontend/src/components/cards/AgentInsightCardThesisVisibility.test.tsx` — 3 new tests for
  forbidden bullish phrases on insufficient-data cards
- `docs/ai/HANDOFF.md` — this entry

## Acceptance criteria met
1. No insufficient-data card displays bullish ACTION language.
2. No HOLD/LOW insufficient-data card describes itself as a high-conviction idea.
3. WHY/ACTION fields are also conservative when `intel_read.insufficient_data=True`.
4. "Why this view?" summary names which signals are trusted vs incomplete (already signal-specific
   from signal-list templates; conservative_why also names signals for WHY section).
5. Missing intel_read degrades gracefully without bullish filler (conservative_* fields absent,
   LLM fields still shown — but insufficient_data gate only fires when intel_read is present).
6. Raw metric keys not leaked (no changes to leak-guard path; conservative copy uses only plain-English).
7. Business Read remains hidden (unchanged).

## Explicit non-changes
- No Deploy changes.
- No allocation math changes.
- No score threshold changes.
- No Supabase SQL/migrations.
- No LLM calls or model behavior changes.
- No frontend component rendering changes.
- No app-wide redesign.

---

## Last change
Intel card consistency + page-load intel_read hydration fix (PR: "fix(intel-card): fix intel_read hydration on page load and BUY/WATCH posture contradiction").

## Root cause
Two separate issues caused Intel card contradictions after the reasoning_v2 UI PR:

**1. Hydration gap (page load)**
`_build_intel_read_for_card` only read `_reasoning_v2` from the card's own `agent_run_id` run.
Active recommendations can bind to old runs that predate `_reasoning_v2`. On page load those runs
lacked `_reasoning_v2`, so `intel_read` was null. After clicking Run Agents, new recommendations
bound to a fresh run with `_reasoning_v2`, so intel_read populated correctly.
Fix: the existing `latest_for_thesis` query (already fetching allocation for latest 5 completed
runs) now also tracks `latest_reasoning_v2_run_id`. `_build_intel_read_for_card` gains a
`fallback_run_id` parameter and uses this latest run when the card's primary run lacks `_reasoning_v2`.

**2. BUY/HIGH CONVICTION vs WATCH contradiction**
Card `action`/`conviction_level` come from the legacy LLM recommendations path (BUY, HIGH).
`intel_read` comes from the deterministic `_reasoning_v2` builder (WATCH, INSUFFICIENT_DATA).
These two signal paths could contradict per-card, producing "Why this view?" saying
"stays on watch instead of becoming a high-conviction idea" on a card showing BUY/HIGH CONVICTION.
Fix: `build_intel_read` now returns an `insufficient_data: bool` backend hint (not rendered by
frontend `WhyThisView`). In card assembly, when `intel_read.insufficient_data=True`, the card's
`action` is downgraded from BUY → HOLD and `conviction_level` from HIGH → LOW before constructing
the `InsightCard`. This enforces one source of truth: the deterministic INSUFFICIENT_DATA WATCH
signal wins over the stale LLM BUY label.

## Files changed
- `v2/backend/app/services/intelligence/reasoning_v2_plain_english.py` — add `insufficient_data: bool` to `build_intel_read` output
- `v2/backend/app/services/recommendation_engine.py` — `_build_intel_read_for_card` gains `fallback_run_id`; thesis contract block extended to also track `latest_reasoning_v2_run_id`; card assembly applies BUY→HOLD downgrade when `intel_read.insufficient_data`
- `v2/backend/tests/test_reasoning_v2_plain_english.py` — 5 new tests for `insufficient_data` flag
- `v2/backend/tests/test_intel_read_projection.py` — updated local `_build_intel_read_for_card` copy to include fallback; 9 new tests for fallback behavior, ticker-absent no-fallback, BUY→HOLD downgrade, and `insufficient_data` flag
- `docs/ai/HANDOFF.md` — this entry

## Acceptance criteria met
1. Page load: if latest run has `_reasoning_v2`, cards now include intel_read (not null).
2. No card shows BUY / HIGH CONVICTION while intel_read says WATCH/INSUFFICIENT_DATA.
3. INSUFFICIENT_DATA WATCH cards show HOLD / LOW CONVICTION consistent with intel_read.
4. Constructive (ACCUMULATE) cards with `insufficient_data=False` are not downgraded.
5. No raw metric keys rendered (unchanged invariant).
6. Business Read remains hidden (unchanged invariant).

## Explicit non-changes
- No Deploy changes.
- No allocation math changes.
- No score threshold changes.
- No Supabase SQL/migrations.
- No LLM calls or model behavior changes.
- No frontend component changes.
- No app-wide redesign.

---

## Last change
Intel Reasoning v2 UI: surface reasoning_v2 coverage as "Why this view?" on AgentInsightCard (PR: "feat(intel-ui): surface reasoning_v2 coverage as plain-English Why this view section").

## intel_read contract (reasoning_v2 plain-English projection)

**New backend translator**: `v2/backend/app/services/intelligence/reasoning_v2_plain_english.py`
- Pure function `build_intel_read(r2: dict) -> Optional[dict]`
- Input: full `reasoning_v2` dict from `agent_runs.allocation["_reasoning_v2"][ticker]`
- Output keys: `title`, `posture_label`, `summary`, `trusted_signals`, `incomplete_signals`, `caveat`
- Contains NO raw metric keys (`momentum_score`, `valuation_score`, `fcf_margin`, etc.)
- Returns `None` when input is not a valid dict

**Dimension label mapping** (published_dimensions use `_score` suffix; suppressed_dimensions use bare names):
- `quality_score` / `quality` → "business quality"
- `valuation_score` / `valuation` → "valuation"
- `growth_score` / `growth` → "growth"
- `risk_score` / `risk` → "risk"
- `momentum_score` / `momentum` → "recent market behavior"

**Posture label mapping**:
- ACCUMULATE → "constructive"
- HOLD → "neutral"
- TRIM → "cautious"
- AVOID → "cautious"
- WATCH → "on watch"

**API field**: `InsightCard.intel_read: Optional[dict] = None` (additive, backward-compatible)
- Populated in `_compute_insight_cards` via `_build_intel_read_for_card(ticker, run_id, run_lookup)`
- Reads `allocation["_reasoning_v2"][ticker]` from same run_lookup as thesis fields
- If `_reasoning_v2` is absent or ticker missing, field is `null` safely (no fallback needed)

**Frontend contract**:
- `IntelRead` interface added to `v2/frontend/src/lib/api.ts`
- `InsightCardData.intel_read?: IntelRead | null` added
- `AgentInsightCard.tsx` renders compact `WhyThisView` component when `intel_read` is present
- `WhyThisView` displays: title, summary, trusted_signals (green chips), incomplete_signals (muted chips), caveat
- When `intel_read` is `null`/`undefined`, section is omitted entirely

**Raw metric leak guardrail**:
- Translator output only ever contains hardcoded plain-English strings from `_EV_KEY_TO_LABEL` / `_DIM_KEY_TO_LABEL`
- No raw metric keys (`momentum_score`, `valuation_score`, `fcf_margin`, etc.) appear in any output field
- Backend test `test_no_raw_metric_keys_in_output` locks this invariant

**Business Read remains hidden**: `thesis_plain_english` is still NOT rendered by `AgentInsightCard`. The new "Why this view?" section is separate and driven by `_reasoning_v2` coverage, not `_thesis_v2`.

## Explicit exclusions
- No Deploy changes.
- No allocation math changes.
- No score math changes.
- No SQL/Supabase migration.
- No LLM calls.
- No Business Read UI re-enable.
- No app-wide redesign.

---

## Last change
Intel Reasoning v2 thesis-v2 acceleration: coverage aggregation + safe info-derived input coverage (PR: "feat(intel-v2-pr5): reasoning_v2 coverage aggregation + safe info-derived input coverage").

## Combined PR scope (PR A + PR B-1 safest part)
This PR combines:
- **Part 1 (PR A)**: Add `reasoning_v2.evidence.deterministic.coverage` aggregation block.
- **Part 2 (PR B-1 safe)**: Add 4 safe info-derived mapper inputs from existing yfinance info/fundamentals payload: `gross_margin`, `fcf_to_net_income`, `p_fcf`, `fcf_yield`.

## Coverage block contract (reasoning_v2.evidence.deterministic.coverage)

Shape:
```json
{
  "published_dimensions": ["momentum_score", "valuation_score"],
  "suppressed_dimensions": ["quality", "growth", "risk"],
  "inputs_used": ["forward_pe", "return_30d", "return_5d", "trailing_pe"],
  "inputs_missing": ["p_fcf"]
}
```

- `published_dimensions`: sorted list of thesis subscore evidence keys present in deterministic (e.g., `["momentum_score", "valuation_score"]`).
- `suppressed_dimensions`: thesis dimensions with `published=False` in the scorecard.
- `inputs_used`: sorted union of `inputs_used` from all published dimensions.
- `inputs_missing`: sorted union of `inputs_missing` from all published dimensions.
- Empty shape `{published_dimensions:[], suppressed_dimensions:[], inputs_used:[], inputs_missing:[]}` returned when no thesis dimensions are published or scorecard is absent.
- WATCH/INSUFFICIENT_DATA enforcement is unchanged.
- Agreement logic is unchanged.
- Analyst evidence is unchanged.

## Safe input mappings added (Part 2)

| Field | Source | Derivation |
|---|---|---|
| `gross_margin` | `yfinance info.grossMargins` | Direct pass-through when numeric |
| `fcf_to_net_income` | `free_cash_flow / net_income` | Only when denominator non-zero |
| `p_fcf` | `market_cap / free_cash_flow` | Only when both strictly positive |
| `fcf_yield` | `free_cash_flow / market_cap` | Only when market_cap > 0 |

Provider additions: `gross_margin` (`grossMargins`) and `net_income` (`netIncomeToCommon`) added to `fetch_yfinance_fundamentals_sync` payload (additive, no existing keys removed).

## Explicit exclusions / deferred to future PRs
- No income_stmt, cash_flow, growth_estimates, insider_transactions, SEC filings, or new provider endpoints fetched.
- No history window extension to 1 year.
- No `max_drawdown_1y`, `revenue_cagr_3y`, `fcf_cagr_3y`, `gross_profit_yoy`.
- No ETF/crypto asset-class diagnostics.
- No `thesis_engine` threshold or score math changes.
- No gate loosening.
- No unsafe proxies: `profit_margin→fcf_margin`, `return_on_equity→roic_ttm`, `debt_to_equity→net_debt_to_ebitda`, `dividend_yield→fcf_yield`, `earnings_growth→forward_revenue_growth_est` all remain blocked.
- No reasoning_v2 API/UI exposure.
- No frontend, Deploy, or SQL changes.
- No LLM prompt/model behavior changes.
- No Business read UI re-enable.

## Confirmation of non-changes
- No frontend/UI changes.
- No Deploy changes.
- No Supabase SQL/migrations.
- No LLM prompt/model behavior changes.
- No score math changes.

## Post-merge Supabase validation query for NVDA/GOOGL/META
```sql
SELECT
  allocation->'_reasoning_v2'->'NVDA'->'evidence'->'deterministic'->'coverage' AS nvda_coverage,
  allocation->'_reasoning_v2'->'GOOGL'->'evidence'->'deterministic'->'coverage' AS googl_coverage,
  allocation->'_reasoning_v2'->'META'->'evidence'->'deterministic'->'coverage' AS meta_coverage,
  allocation->'_reasoning_v2'->'NVDA'->'data_quality'->'status' AS nvda_dq_status
FROM agent_runs
WHERE status = 'completed'
ORDER BY finished_at DESC
LIMIT 1;
```

---

## Last change
Intel Reasoning v2 PR 4: preserve published deterministic evidence under INSUFFICIENT_DATA (PR: "fix(intel-v2-pr4): preserve published deterministic dimensions in reasoning_v2").

## Exact root cause
- `reasoning_v2_builder` gated deterministic extraction on `scorecard.status != INSUFFICIENT_DATA`.
- Live thesis_v2/ScoreCard payloads can validly contain `published=true` dimensions (e.g., valuation/momentum) while overall status remains `INSUFFICIENT_DATA`.
- Because of the status gate, `evidence.deterministic` was emptied and agreement defaulted to `analyst_only` even when deterministic dimensions were published.

## What was fixed
- Removed the hard status gate for deterministic extraction and now preserve published thesis dimensions regardless of overall status.
- Deterministic thesis dimension evidence now keeps machine-readable fields: `score`, `published`, `inputs_used`, `data_quality`, `inputs_missing`.
- Agreement logic now considers actual published deterministic presence, so INSUFFICIENT_DATA + published deterministic dimensions no longer collapses to `analyst_only`.
- Safety contract unchanged: INSUFFICIENT_DATA still forces WATCH posture and insufficient_data blocker.

## Tests added/updated
- Added live-style INSUFFICIENT_DATA thesis regression tests where valuation/momentum are published and quality/growth/risk remain suppressed.
- Verified deterministic evidence inclusion, WATCH safety persistence, non-analyst_only agreement when deterministic evidence exists, and user-text non-leakage of raw advanced metric keys.

## Confirmation of non-changes
- No frontend/UI changes.
- No Deploy changes.
- No Supabase SQL/migrations.
- No LLM prompt/model behavior changes.

---

## Last change
Intel thesis_v2 diagnostics live serialization fix (PR: "fix(intel-v2): persist thesis_v2 diagnostics in live allocation payload").

## Exact root cause
- Prior diagnostics work only hardened `reasoning_v2_builder.data_quality`; it did **not** add a `diagnostics` field to the serialized `_thesis_v2` scorecard persisted by orchestrator.
- Live persistence path is `orchestrator._scorecard_to_dict() -> allocation["_thesis_v2"][ticker]`.
- That serializer emitted status/subscores/inputs but no diagnostics key, so Supabase queries like `allocation->'_thesis_v2'->'NVDA'->'diagnostics'` returned `null` for every ticker.

## What was fixed
- Added deterministic `diagnostics` construction inside `orchestrator._scorecard_to_dict()` and persisted it into each `_thesis_v2` ticker object.
- Contract now includes:
  - `missing_dimensions`
  - `suppressed_dimensions`
  - `published_dimensions`
  - `unpublished_reasons`
  - `user_safe_note`
- No score math or quality-gate logic changed; this is serialization/observability only.

## Regression coverage added
- Test ensures INSUFFICIENT_DATA thesis payload can carry non-null diagnostics pre-serialization.
- Test ensures orchestrator live serialization of INSUFFICIENT_DATA ScoreCard includes non-null diagnostics in exact `_thesis_v2` shape.

## Post-merge Supabase verification query
```sql
SELECT
  allocation->'_thesis_v2'->'NVDA'->>'status' AS nvda_status,
  allocation->'_thesis_v2'->'NVDA'->'diagnostics' AS nvda_diagnostics,
  allocation->'_thesis_v2'->'GOOGL'->'diagnostics' AS googl_diagnostics,
  allocation->'_thesis_v2'->'META'->'diagnostics' AS meta_diagnostics
FROM agent_runs
WHERE status = 'completed'
ORDER BY finished_at DESC
LIMIT 1;
```

## Confirmation of non-changes
- No frontend/UI changes.
- No Deploy changes.
- No Supabase SQL/migrations.
- No LLM prompt/model behavior changes.


---

## Last change
Intel Reasoning v2 PR 3: actionable INSUFFICIENT_DATA diagnostics for thesis_v2 scorecards (PR: "fix(intel-v2-pr3): make insufficient thesis diagnostics actionable").

## Exact root cause
- `reasoning_v2_builder._build_data_quality()` only read `scorecard["missing_fields"]` / `scorecard["stale_fields"]`.
- Live thesis_v2 scorecards persisted by orchestrator (`_scorecard_to_dict`) use `inputs_missing` / `inputs_used` and do **not** include `missing_fields`.
- Result: `data_quality.status` correctly stayed `INSUFFICIENT_DATA`, but diagnostics collapsed to `missing=[]` and `stale=[]`.
- `evidence.deterministic` stayed `{}` by design for INSUFFICIENT_DATA (WATCH hardening), so there was no alternate clue for why data was thin.

## What was fixed
- Added backward-compatible diagnostics fallback in `reasoning_v2_builder`:
  - read `missing_fields` first, fallback to `inputs_missing` for serialized thesis_v2 scorecards.
  - detect suppressed thesis dimensions (`quality`, `valuation`, `growth`, `risk`, `momentum`) when `published=False`.
  - for INSUFFICIENT_DATA with empty missing/stale but suppressed dimensions present, inject actionable missing markers (`suppressed:<dimension>`) and append explicit suppression detail to `user_safe_note`.
- Kept all guardrails intact:
  - INSUFFICIENT_DATA still forces WATCH.
  - deterministic evidence remains empty under INSUFFICIENT_DATA (no score leakage).
  - no threshold/score math relaxation.

## Verification findings (live-contract equivalent)
- The issue was a **diagnostics key mismatch + suppression observability gap**, not a reasoning_v2 WATCH-gate bug.
- Mapper coverage for safe stock fields remains intact; no unsafe proxy mapping added.
- Asset-type gating was not changed in this patch; diagnostics are now honest when scorecards are insufficient regardless of ticker class.

## Tests added
- Reproduced live-style failure mode: serialized thesis scorecard with `inputs_missing` and no `missing_fields` now reports actionable `data_quality.missing`.
- Added guard test ensuring INSUFFICIENT_DATA cannot surface with both `missing=[]` and `stale=[]` when dimensions are suppressed.

## Files touched
- `v2/backend/app/services/intelligence/reasoning_v2_builder.py`
- `v2/backend/tests/test_reasoning_v2_builder.py`
- `docs/ai/HANDOFF.md`
- `v2/progress_log.md`

## Confirmation of non-changes
- No frontend/UI changes.
- No Deploy changes.
- No Supabase SQL/migrations.
- No LLM prompt/model behavior changes.

---



## Last change
Intel Reasoning v2 PR 2: deterministic thesis scorecard fusion into reasoning_v2 (PR: "feat(intel-v2-pr2-reasoning): fuse thesis_v2 scorecard into reasoning_v2 evidence").

## PR 2 deterministic-fusion behavior

**Root cause of analyst-only reasoning_v2 (resolved)**
The wire-up in `orchestrator.py` always passed `scorecard=None` to `build_reasoning_v2`. The `_thesis_scorecards` dict (computed in Phase 2.5) was never forwarded to the reasoning_v2 builder, so `evidence.deterministic` was always `{}` and `confidence.agreement` was always `"analyst_only"`.

**Exact wire-up fix point**
`v2/backend/app/services/agents/orchestrator.py` — Intel Reasoning v2 block (lines ~462–487).
`_r2_scorecard = _r2_thesis_cards.get(_r2_ticker)` now extracts the same `score_schema.ScoreCard` object stored under `_thesis_v2` for each ticker, and passes it to `build_reasoning_v2` as `scorecard=`.

**What deterministic fusion now does**
- `score_schema.ScoreCard` objects (live Phase 2.5 output) are detected by duck-typing and normalised via `_normalize_thesis_engine_scorecard()`.
- Serialized thesis dicts (from `_scorecard_to_dict`, stored in `_thesis_v2`) are also accepted as plain dicts.
- Both paths produce identical `evidence.deterministic` contents (test PR2-3 verifies parity).
- Only **published** thesis subscores appear in `evidence.deterministic` (quality_score, valuation_score, growth_score, risk_score, momentum_score — only when `subscore.published=True`).
- Agreement is derived from `conviction_band`: HIGH/MEDIUM → positive direction; LOW → negative direction (falls back to legacy sentiment_score/return_30d for stub scorecards).
- Agree + BUY analyst → `confidence.agreement="agree"`, non-WATCH posture.
- Disagree (e.g., LOW conviction thesis + BUY analyst) → `confidence.agreement="disagree"`, `action.posture=WATCH`, `deploy_signals.action_posture=WATCH`, `blockers=["agreement_conflict"]`.
- INSUFFICIENT_DATA thesis status → existing hardening rule still forces WATCH; `evidence.deterministic={}`.
- PARTIAL thesis status with useful published dimensions → those dimensions appear in `evidence.deterministic` honestly.

**Confirmation: reasoning_v2 remains backend-only and dormant**
- `_reasoning_v2` is written to `agent_runs.allocation` JSONB only.
- No API endpoint exposes it. No InsightCard field added. No frontend change.
- No Deploy change. No SQL migration. No LLM behavior change.

**Future PR 3 guidance**
Before any API/UI exposure of `_reasoning_v2`, inspect live output from a completed run:
1. Confirm `evidence.deterministic` is non-empty for tickers with READY/PARTIAL thesis data.
2. Confirm `confidence.agreement` reflects the actual thesis/analyst signal alignment.
3. Confirm `deploy_signals.blockers` correctly categorises conflict vs insufficient cases.
4. Only then project `reasoning_v2` onto InsightCard or a new endpoint.

## Post-merge Supabase query to inspect deterministic evidence for NVDA/GOOGL/META

```sql
SELECT
  allocation->'_reasoning_v2'->'NVDA'->'evidence'->'deterministic' AS nvda_det,
  allocation->'_reasoning_v2'->'GOOGL'->'evidence'->'deterministic' AS googl_det,
  allocation->'_reasoning_v2'->'META'->'evidence'->'deterministic'  AS meta_det,
  allocation->'_reasoning_v2'->'NVDA'->'confidence'->'agreement'   AS nvda_agreement,
  allocation->'_reasoning_v2'->'NVDA'->'data_quality'->'status'    AS nvda_dq_status
FROM agent_runs
WHERE status = 'completed'
ORDER BY finished_at DESC
LIMIT 1;
```

## Files touched
- `v2/backend/app/services/agents/orchestrator.py` — pass `_thesis_scorecards.get(ticker)` to `build_reasoning_v2` instead of `None`.
- `v2/backend/app/services/intelligence/reasoning_v2_builder.py` — add `_THESIS_SUBSCORE_MAP`; add `_normalize_thesis_engine_scorecard()` + `_EmptySubScore`; update `_normalize_scorecard()` for duck-typed `score_schema.ScoreCard`; update `_sc_quality()` to accept `blended_data_quality`; update `_build_published_dimensions()` and `_build_evidence()` to extract published thesis subscores; update `_derive_agreement()` to use `conviction_band` for direction.
- `v2/backend/tests/test_reasoning_v2_builder.py` — added 18 new focused tests (PR2-1 through PR2-11) for thesis fusion; all existing 47 tests continue to pass.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Confirmation of non-changes
- No frontend/UI change.
- No Deploy code change.
- No Supabase SQL/migration change.
- No LLM prompt/model/analyst generation change.
- `_thesis_v2` untouched (still serialized by existing `_scorecard_to_dict` path).
- No score math changes.

---

## Last change
Intel Reasoning v2 PR-1 contract hardening after live-run inspection (PR: "fix(intel-v2): enforce insufficient-data WATCH contract in reasoning_v2").

## Live-run inspection result
- Confirmed on Supabase completed run output: `agent_runs.allocation["_reasoning_v2"]` exists and `_thesis_v2` remains present/dormant.
- Live `NVDA` `reasoning_v2.0` sample showed contract inconsistency: `data_quality.status=INSUFFICIENT_DATA` while `action.posture=ACCUMULATE`, `deploy_signals.action_posture=ACCUMULATE`, and `confidence.conviction_band=HIGH`.

## Exact contract inconsistency fixed
- Previous builder behavior allowed strong analyst BUY verdicts to set high-conviction ACCUMULATE even when deterministic status was `INSUFFICIENT_DATA`.
- This could leak unsafe posture into downstream deploy consumers if they read `reasoning_v2`.

## Fixed rule now enforced
- If `data_quality.status == INSUFFICIENT_DATA`, force:
  - `action.posture = WATCH`
  - `deploy_signals.action_posture = WATCH`
  - `deploy_signals.blockers` includes `insufficient_data`
  - high conviction is prevented (`confidence.conviction_band` and `deploy_signals.conviction_band` are downgraded from HIGH to `INSUFFICIENT_DATA`).
- Analyst evidence remains preserved under `evidence.analyst` for traceability and review.
- Analyst copy can still inform non-action evidence fields, but it no longer overrides WATCH posture under insufficient data.

## Files touched
- `v2/backend/app/services/intelligence/reasoning_v2_builder.py` — added deterministic insufficient-data contract enforcement helper and wired it before payload assembly.
- `v2/backend/tests/test_reasoning_v2_builder.py` — updated/added focused tests for insufficient-data + strong analyst BUY WATCH forcing, conviction downgrade, and analyst evidence preservation; fallback/no-leakage tests remain in suite.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Confirmation of non-changes
- No frontend/UI change (reasoning_v2 remains non-exposed).
- No Deploy code change.
- No Supabase SQL/migration change.
- No LLM prompt/model/analyst generation change.
- `_thesis_v2` untouched.

# AI Engineering Handoff

## Intel Reasoning v2 — PR 1 (Dormant Backend Builder)

**Status**: Merged — live on `claude/reasoning-v2-builder-Ujp9G`
**Date**: 2026-05-02
**Author**: Claude (automated)

### Scope

PR 1 implements the deterministic backend builder for Intel Reasoning v2.
It fuses analyst verdict data into a structured reasoning object and persists
it dormant inside `agent_runs.allocation["_reasoning_v2"]` per ticker.

**This PR is intentionally minimal.** Reasoning v2 is NOT exposed on any API
endpoint or InsightCard model. It must be inspected directly from the
`agent_runs` table until live-run output has been reviewed (see PR 2 guidance below).

### New module

`v2/backend/app/services/intelligence/reasoning_v2_builder.py`

- Pure deterministic function: `build_reasoning_v2(*, ticker, scorecard, analyst_verdict, provider_meta)`
- Accepts `ScoreCard` dataclass, serialised dict, or `None`
- Output schema version: `reasoning_v2.0`
- Top-level sections: `why`, `risk`, `action`, `alt_view`, `confidence`, `deploy_signals`, `evidence`, `data_quality`
- Forbidden indicator language scrubbed from all `user_text` fields
- No allocation math, dollar amounts, or position targets in output
- `deploy_signals` is metadata-only (bands, blockers, caveats — no sizing)

### Persistence

`agent_runs.allocation["_reasoning_v2"]` is a dict keyed by ticker symbol.
Each value is the full `reasoning_v2.0` structured object from the builder.

The `_reasoning_v2` key is written alongside existing per-ticker allocation
amounts in the same `agent_runs.allocation` JSONB column. No SQL migration
is required.

Wire-up site: `v2/backend/app/services/agents/orchestrator.py` — immediately
after the `allocation_map` dict comprehension in `Orchestrator.run()`.

Builder failures are caught per-ticker; a single ticker failure does not
break Run Agents or recommendation generation.

### What is NOT changed

| Area | Status |
|---|---|
| `InsightCard` model | **Not changed** — `reasoning_v2` field not added in PR 1 |
| `GET /api/v1/recommendations/` | **Not exposed** — `_reasoning_v2` stays in allocation only |
| Frontend / UI | **No change** |
| Deploy flow | **No change** |
| Score / recommendation math | **No change** |
| LLM prompts or model choices | **No change** |
| `thesis_plain_english` / Business read UI | **Remains hidden/dormant** |
| Supabase SQL migrations | **None required** |

### Current limitations (PR 1)

- `scorecard` is always `None` at the wire-up site because `thesis_engine.py` /
  `score_schema.py` do not yet exist. The `ScoreCard` stub in the builder is
  forward-compatible.
- `evidence.deterministic` is always `{}` for all tickers until a scorecard
  pipeline is implemented.
- `why.support` will be `"analyst"` for valid verdicts, `"insufficient"` otherwise.
- Agreement is `"analyst_only"` until scorecard dimensions are populated.

### How to inspect `_reasoning_v2` after a live Run Agents execution

Run the following SQL in Supabase (replace `<user_id>` and `<run_id>`):

```sql
SELECT
  id,
  status,
  finished_at,
  allocation->'_reasoning_v2' AS reasoning_v2
FROM agent_runs
WHERE user_id = '<user_id>'
  AND status = 'completed'
ORDER BY finished_at DESC
LIMIT 1;
```

To inspect a single ticker:

```sql
SELECT
  allocation->'_reasoning_v2'->'NVDA' AS nvda_reasoning_v2
FROM agent_runs
WHERE user_id = '<user_id>'
ORDER BY finished_at DESC
LIMIT 1;
```

### PR 2 guidance

PR 2 should intentionally expose / project `reasoning_v2` onto the InsightCard
or a dedicated endpoint **only after** PR 1 live-run output has been inspected
on real portfolios. Specifically:

1. Confirm `_reasoning_v2` is populated for every ticker in a completed run.
2. Confirm `why.user_text` is plain-English and free of forbidden language.
3. Confirm `deploy_signals.blockers` correctly reflects missing/conflict states.
4. Confirm no allocation math keys appear anywhere in the output.
5. Only then add `reasoning_v2` to `InsightCard` or a new endpoint.

**Do not start PR 2 until live-run inspection is complete.**
## Last change
Intel UI cleanup: hide Business read section on live Intel cards (PR: "fix(intel-v2): hide Business read on AgentInsightCard").

## Files touched
- `v2/frontend/src/components/cards/AgentInsightCard.tsx` — Removed Business read rendering block from live Intel card surface; WHY/RISK/ACTION/ALT VIEW sections remain unchanged.
- `v2/frontend/src/components/cards/AgentInsightCardThesisVisibility.test.ts` — Replaced visibility tests with assertions that cards still render WHY/RISK/ACTION/ALT VIEW and do not render Business read, even when `thesis_plain_english` exists in payload; retained raw metric key non-visibility checks.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Product decision
- Business read UI is hidden for now because live signal quality remains low-value and repetitive across cards.
- Backend `thesis_plain_english` generation/storage remains intact and dormant for future re-enable.
- Re-enable gate: show real-run payload proof across representative tickers with clearly differentiated, useful output before restoring UI visibility.

## Confirmation of non-changes
- No score math changes.
- No LLM behavior/prompt changes.
- No Deploy changes.
- No Supabase SQL changes.
- No Run Agents lifecycle changes.

---

## Last change
Intel Business read end-to-end freshness repair (PR: "fix(intel-v2): end-to-end Business read freshness and coverage repair").

## Files touched
- `v2/backend/app/services/recommendation_engine.py` — Two changes:
  (1) `_build_thesis_fields_for_card` now accepts `fallback_run_id`; when the card's own `agent_run_id` run is absent from `run_lookup` or its allocation lacks `_thesis_v2`, it tries the fallback run and returns diagnostic code `attached_via_latest_run`.
  (2) `_compute_insight_cards` now queries the latest 5 completed runs after building `run_lookup` to find `latest_thesis_run_id` (most recent completed run with non-empty `_thesis_v2`); adds it to `run_lookup` if not already present; logs it as `thesis.contract`; passes it as `fallback_run_id` to every `_build_thesis_fields_for_card` call.
- `v2/backend/tests/test_recommendation_engine.py` — Added `TestThesisRunFallbackBehavior` class (7 tests): old run lacks `_thesis_v2` → fallback serves varied labels; varied labels per ticker via fallback; both runs lack thesis → safe None; run_not_found falls back; primary wins when it already has thesis; same run_id not double-used; None fallback degrades gracefully.
- `v2/frontend/src/components/cards/AgentInsightCardThesisVisibility.test.ts` — Extended with 5 new tests: per-ticker varied Business read lines, all 7 field types rendered, empty-string field omission, missing caveats array safety, invalidation query key contract.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Exact root cause
Three overlapping issues all converge to the same symptom (universal identical Business read fallback):

1. **Stale run binding** (primary): `recommendations.agent_run_id` on active cards points to an older run whose `agent_runs.allocation` pre-dates `_thesis_v2` (introduced in PR-2). This happens when `_persist` fails silently — the orchestrator catches the exception (`persistence_warning`) and continues, but old recommendations remain active with old `agent_run_id`s. Those old runs have no `_thesis_v2`.

2. **Timing race** (secondary): `_persist.finally` calls `invalidate_recommendations_aggregate_cache` immediately after writing new recommendations. If any request (e.g., from `get_job_status` polling) hits `get_insight_cards` in the small window before `_update_run` writes `_thesis_v2` to `agent_runs.allocation`, the backend aggregate cache is populated with no-thesis results. The 20-second TTL then serves this stale result.

3. **No fallback path** (design gap): `_build_thesis_fields_for_card` only tried the card's own `agent_run_id` run. There was no fallback to the latest completed run that has `_thesis_v2`.

## Final end-to-end contract (after fix)
1. User clicks Run Agents → `POST /api/v1/recommendations/refresh` → backend creates new run or returns `in_progress` for active run
2. Frontend sets `activeJobId`, polls `GET /api/v1/recommendations/jobs/{job_id}` every 3 seconds
3. Orchestrator: Phase 2.5 computes `_thesis_scorecards`; after LLM phase, `_persist` inserts new recommendations with new `agent_run_id` and expires old ones; `_update_run(status="completed", allocation=allocation_map)` writes `_thesis_v2` to `agent_runs.allocation`
4. Frontend detects `status="completed"` → invalidates `["recommendations"]` cache → calls `refetchQueries`
5. `GET /api/v1/recommendations/` → `_compute_insight_cards`:
   - Fetches active recommendations (new ones from step 3)
   - Builds `run_lookup` from their `agent_run_id`s
   - **NEW**: queries latest 5 completed runs, finds `latest_thesis_run_id` (newest with `_thesis_v2`), adds to `run_lookup`, logs `thesis.contract`
   - For each card: calls `_build_thesis_fields_for_card(fallback_run_id=latest_thesis_run_id)`
   - **NEW**: if card's own run lacks `_thesis_v2`, uses fallback run; logs `thesis.fallback_used`
   - Generates `thesis_plain_english` from the best-available scorecard
6. Frontend receives cards with `thesis_plain_english` populated (if any run has data)
7. `AgentInsightCard` renders Business read section

## How to verify manually after deployment
1. Click Run Agents
2. Wait for progress tracker to show completion (or poll manually if in_progress)
3. Confirm recommendations list is refetched (network tab shows new GET /recommendations/ request)
4. Inspect GOOGL, META, NVDA cards — Business read section should be present
5. Check backend logs for `thesis.contract` log showing `latest_thesis_run=<uuid>` (not `none`)
6. If fallback was used, check for `thesis.fallback_used` log per ticker
7. Business read quality labels should differ between tickers when `_thesis_v2` dimensions vary

## Confirmation of invariants
- No score math changes.
- No LLM behavior or prompts changed.
- No Deploy changes.
- No Supabase SQL or migrations (all reads from existing JSONB columns).
- No frontend/UI redesign.

---

## Last change
Intel live-contract diagnostic: business-read repetition root-cause verification (PR: "test(intel-v2): add live-style thesis contract diagnostic").

## Files touched
- `v2/backend/tests/test_recommendation_engine.py` — Added focused contract test (`TestLiveStyleSerializedThesisContract`) that simulates a live-style serialized `agent_runs.allocation["_thesis_v2"]` payload for `GOOGL`, `META`, and `NVDA` and verifies `_build_thesis_fields_for_card()` + `build_thesis_plain_english()` produce per-ticker directional labels (not a universal incomplete fallback) while preserving `INSUFFICIENT_DATA` headline semantics.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise diagnostic entry added.

## Exact diagnosis
- Backend translator branch is already updated: for `INSUFFICIENT_DATA` it reuses computed per-dimension labels when available, not hardcoded universal "dimension incomplete" strings.
- Intel page fetch path is `GET /api/v1/recommendations/` (`useRecommendations`), rendered via `AgentInsightCard` on `/dashboard/recommendations`.
- "Run Agents" calls `POST /api/v1/recommendations/refresh` and may reuse an existing active run (`status=in_progress`) by design; it does not guarantee a brand-new run id when one is already active.
- Each recommendation card binds to its own persisted `recommendations.agent_run_id`; card thesis payload comes from that run’s `agent_runs.allocation["_thesis_v2"]` map.
- Therefore repeated identical business-read copy across cards is primarily a data/run-selection contract issue (same/old run payload attached to many cards, or scorecards lacking published dimension variation), not the translator logic itself.

## Final behavior
- Added regression-level proof that live-style serialized `_thesis_v2` with varied published dimensions yields varied `thesis_plain_english` labels across tickers.
- No score math changes, no data-quality gate changes, no LLM changes, no deploy changes, no Supabase SQL.

## Last change
Intel v2 thesis plain-English data-quality regression fix: avoid universal incomplete fallback for data-bearing insufficient scorecards (PR: "fix(intel-v2): preserve directional dimension labels for insufficient scorecards").

## Files touched
- `v2/backend/app/services/intelligence/thesis_plain_english.py` — Narrowed only the `INSUFFICIENT_DATA` plain-English branch: keeps conservative headline/data label/caveats, but now reuses computed per-dimension labels (`quality`/`valuation`/`risk`/`momentum`) instead of hardcoded identical "data is incomplete" strings.
- `v2/backend/tests/test_thesis_plain_english.py` — Added regression tests proving: (1) data-bearing insufficient scorecards do not collapse into universal fallback labels, and (2) serialized dict input produces same plain-English output as ScoreCard object input.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Exact root cause
- Pipeline wiring from collected provider data → `score_thesis()` → `_thesis_v2` storage → `InsightCard.thesis_plain_english` was functioning.
- Regression was in the translator behavior for `INSUFFICIENT_DATA`: `build_thesis_plain_english()` used a fully hardcoded fallback block that ignored available published dimension subscores in the scorecard.
- Because many live tickers legitimately remain `INSUFFICIENT_DATA` under unchanged quality gates, every visible card rendered nearly identical Business read copy.

## Final behavior
- `INSUFFICIENT_DATA` remains conservative and honest (same status semantics, same cautionary posture).
- When an insufficient scorecard still has some published dimensions, those directional labels now surface, so cards no longer all read identically.
- No score math changes, no mapper/proxy fabrication, no data-gate loosening, no LLM changes, no Deploy changes, no Supabase SQL.

---

## Last change
Intel v2 diagnostic fix: thesis_plain_english visibility on live Intel cards (PR: "fix(intel-v2): render thesis_plain_english on AgentInsightCard").

## Files touched
- `v2/frontend/src/components/cards/AgentInsightCard.tsx` — Added `thesis_plain_english` rendering block for the **live Intel card** surface (`AgentInsightCard`) using a compact plain-English section labeled **Business read**; added exported helper `collectIntelThesisLines()` to keep render condition explicit and testable.
- `v2/frontend/src/components/cards/AgentInsightCardThesisVisibility.test.ts` — **new test file**. Adds focused visibility tests that prove lines are emitted when `thesis_plain_english` is present and omitted when null/missing.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Exact root cause
- Backend wiring for `thesis_plain_english` already existed and was attachable when `_thesis_v2` scorecards were present.
- Frontend API typing also already included `thesis_plain_english`.
- **But live Intel page (`/dashboard/recommendations`) renders `AgentInsightCard`, not `InsightCard`.**
- Prior PR rendered `thesis_plain_english` only in `InsightCard.tsx` (unused on the live Intel route), so users only saw old memo sections (WHY/RISK/ACTION/ALT VIEW).

## Final contract behavior
- If backend provides `thesis_plain_english`, live Intel cards now render a visible plain-English block (`Business read`) with headline/labels/caveats.
- If `thesis_plain_english` is null/missing, cards render safely with no extra section.
- No changes to scoring math, LLM behavior, deploy behavior, or Supabase.

---

## Last change
Intel copy cleanup: remove user-facing "thesis" jargon from Intel text (PR: "fix(intel): replace thesis jargon in user-facing copy").

## Files touched
- `v2/backend/app/services/recommendation_engine.py` — Updated user-facing recommendation/synthesis strings from "thesis" phrasing to plain-English "investment case" / "business-case" wording.
- `v2/backend/app/services/intelligence/thesis_plain_english.py` — Updated user-facing headline text from "thesis read" to "investment-case read" while preserving backend field names.
- `v2/frontend/src/components/cards/portfolioSynthesisRuntime.ts` — Updated watchlist fallback copy to "Recheck the business case and evidence".
- `v2/frontend/src/components/cards/InsightCard.tsx` — Changed section label from "Thesis read" to "Investment case read".
- `v2/backend/tests/test_recommendation_engine.py` — Updated copy assertion for declining-case wording.
- `v2/backend/tests/test_thesis_plain_english.py` — Updated expected headline strings.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Copy rule update
- "thesis" is allowed as an internal/backend concept and field name (`thesis_v2`, `thesis_plain_english`), but user-facing copy should use plain-English wording such as "business case", "investment case", "setup", or "reasoning".

## Behavior change
- User-facing Intel/recommendation text no longer surfaces "thesis" in the updated templates/labels.
- No scoring logic changes, no recommendation/allocation/deploy behavior changes, no LLM behavior changes, no Supabase SQL changes.

---

## Last change
Intel v2: improve thesis plain-English card coverage via tolerant thesis_v2 ticker lookup (PR: "fix(intel-v2): improve thesis plain-English card coverage").

## Files touched
- `v2/backend/app/services/recommendation_engine.py` — Added backend-only ticker lookup normalization helpers (`_normalize_ticker_lookup_key`, `_resolve_thesis_scorecard_for_ticker`) and switched thesis scorecard retrieval to tolerant matching across case/separator variants (e.g., `BRK-B`, `brk.b`, `brk b`). Direct key match remains first; no scoring or UI changes.
- `v2/backend/tests/test_recommendation_engine.py` — Added focused tests for ticker normalization, exact key lookup, normalized key lookup, and malformed/missing map safety.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Coverage/wiring rule (updated)
- `InsightCard.thesis_plain_english` generation still requires `agent_run_id` + `agent_runs.allocation["_thesis_v2"]` scorecard presence.
- Scorecard lookup now uses:
  1) exact ticker key match first;
  2) normalized fallback key match using uppercase alphanumeric-only keys for safe symbol format tolerance.
- This improves attachment reliability when ticker formatting differs by case/dot/dash/spacing, while preserving original display ticker symbols.
- If `_thesis_v2` is missing, not a dict, ticker has no match, or scorecard shape is malformed, both `thesis_v2` and `thesis_plain_english` remain omitted safely.

## Behavior change
- Backend card assembly now attaches `thesis_plain_english` for more valid existing `_thesis_v2` scorecards that were previously skipped due to ticker key-format mismatch.
- No score math changes, no LLM behavior changes, no frontend changes, no Deploy changes, no Supabase SQL changes.

---

## Last change
Intel UI label clarification: run-level vs ticker-level data quality labels (PR: "fix(intel): clarify run vs ticker data quality labels").

## Files touched
- `v2/frontend/src/components/cards/DataQualityBanner.tsx` — top quality chip text changed from `Data {label}` to `Run data {label}`.
- `v2/frontend/src/components/cards/PortfolioSynthesisPanel.tsx` — synthesis quality chip text changed from `Data {quality}` to `Run data {quality}`.
- `v2/frontend/src/components/cards/AgentInsightCard.tsx` — per-card chip text changed from `Data: {label}` to `Ticker data: {label}`.
- `v2/progress_log.md` — concise entry added.
- `docs/ai/HANDOFF.md` — this entry.

## Behavior change
- UI copy only: clarifies that top/batch quality is run-level while card quality is ticker-level.
- No scoring/math/data-quality computation changes.
- No backend/API/LLM/Deploy/Supabase changes.

---

## Last change
Frontend CI/testability hardening: make lint/tests/build agent-runnable (PR: "chore(frontend): make lint and tests agent-runnable").

## Files touched
- `v2/frontend/.eslintrc.json` — **new file**. Adds explicit Next.js ESLint config (`next/core-web-vitals`) so `next lint` no longer prompts for interactive initialization.
- `v2/frontend/jest.config.js` — **new file**. Adds deterministic Jest config using `ts-jest`, Node test env, `src` root, and `@/` path alias mapping.
- `v2/frontend/package.json` — updates scripts/dependencies: `lint` now runs non-interactively (`next lint --no-cache`), `test` runs `jest --runInBand`, and adds minimal Jest dev dependencies (`jest`, `ts-jest`, `@types/jest`).
- `v2/frontend/package-lock.json` — lockfile updated for new frontend dev dependencies.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Frontend validation commands (agent/CI-safe)
- Install deps: `cd v2/frontend && npm install`
- Lint (non-interactive): `cd v2/frontend && npm run lint`
- Build with safe placeholder public Supabase vars:
  - `cd v2/frontend && NEXT_PUBLIC_SUPABASE_URL=https://example.supabase.co NEXT_PUBLIC_SUPABASE_ANON_KEY=dummy-anon-key npm run build`
- Test suite: `cd v2/frontend && npm test`
- Focused thesis tests: `cd v2/frontend && npm test -- InsightCardThesis`

## Env notes
- `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` are required for static build paths that import the Supabase client.
- For CI/local validation, use non-secret placeholders (public-style env vars only). Do not commit real keys.

## Behavior change
- No product/runtime behavior changes intended.
- This PR is tooling/testability hardening only (lint/test/build determinism for unattended validation).
- No backend changes, no Supabase SQL, no deploy behavior changes, no LLM behavior changes.

---

## Last change
Intel v2 PR-9: render plain-English thesis on frontend Intel cards (PR: "feat(intel-v2-pr9): render plain-English thesis on Intel cards").

## Files touched
- `v2/frontend/src/lib/api.ts` — Added `thesis_plain_english?: ThesisPlainEnglish | null` to `InsightCardData`. Added new exported `ThesisPlainEnglish` interface with `headline`, `quality_label`, `valuation_label`, `risk_label`, `momentum_label`, `data_label`, `caveats` fields. Additive, backward-compatible.
- `v2/frontend/src/components/cards/InsightCard.tsx` — Imported `ThesisPlainEnglish` type. Added `ThesisReadSection` component rendered inside `InsightCard` when `thesis_plain_english` is present. Section is visually compact: thin divider, "Thesis read" label, headline, label pills, and caveats. Omitted entirely when field is null/undefined.
- `v2/frontend/src/components/cards/InsightCardThesis.test.ts` — **new test file**. 20 contract tests across 5 groups: present fields render, missing thesis does not crash, thesis_v2 never rendered, no raw metric keys in display text, UI binds only to thesis_plain_english.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Frontend consumption rule (PR-9 contract)
- Render `thesis_plain_english` only — never render `thesis_v2` directly in the UI.
- `thesis_v2` is a backend-only raw scorecard dict; it must not appear in user-facing copy.
- Raw metric keys (`fcf_margin`, `roic_ttm`, `ev_ebitda`, `ps_ttm`, `net_debt_to_ebitda`, etc.) must never appear in rendered UI copy.
- `thesis_plain_english` is safe to render: plain-English strings, no raw metric keys.
- If `thesis_plain_english` is null/undefined, omit the section silently.

## QA scope completed
- `npx tsc --noEmit` — no new errors introduced (pre-existing errors are missing node_modules types; unrelated to this PR).
- All errors are from `AgentInsightCard.tsx` (pre-existing JSX type missing), not from changed files.
- `npm run lint` — `next` binary not available in CI env; pre-existing limitation.
- 20 focused contract tests authored in `InsightCardThesis.test.ts`.

## Behavior change
- `InsightCard` now renders a compact "Thesis read" section when `thesis_plain_english` is populated in the API response.
- Section shows: headline (plain-English summary), 1–5 label pills (quality/valuation/risk/momentum/data), and up to N caveats.
- Section is omitted when all sub-fields are null/empty (no visual noise for partial data).
- No backend changes, no Supabase SQL, no LLM calls, no Deploy changes, no allocation math changes.

---

## Last change
Intel v2 PR-8: wire plain-English thesis translator into backend recommendation/intel responses (PR: "feat(intel-v2-pr8): expose plain-English thesis response field").

## Files touched
- `v2/backend/app/models/recommendation.py` — `InsightCard` gains `thesis_plain_english: Optional[dict] = None`. Additive, backward-compatible. Raw metric keys must never appear in this field's text.
- `v2/backend/app/services/recommendation_engine.py` — Imports `build_thesis_plain_english`; `run_lookup` query extended to fetch `allocation` column; `_compute_insight_cards` extracts per-ticker thesis scorecard from `allocation["_thesis_v2"]`, generates `thesis_plain_english` with a broad except guard, and populates both `thesis_v2` and `thesis_plain_english` on `InsightCard`.
- `v2/backend/tests/test_thesis_response_wiring.py` — **new test file**. 22 tests across 5 groups: payload presence, thesis_v2 preservation, raw metric key redaction, missing/partial/INSUFFICIENT_DATA safe handling, and no-IO determinism.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Response field contract (PR-8)
- New field: `thesis_plain_english` — dict with keys: `headline`, `quality_label`, `valuation_label`, `risk_label`, `momentum_label`, `data_label`, `caveats`.
- Present when `allocation["_thesis_v2"][ticker]` exists for the linked agent run; `None` otherwise.
- Contains no raw metric keys (`fcf_margin`, `roic_ttm`, `net_debt_to_ebitda`, `ev_ebitda`, `ps_ttm`, etc.).
- `thesis_v2` (raw scorecard dict) also populated at the same time from the same source.
- Both fields are `None` for pre-PR-2 runs that lack `_thesis_v2` in allocation.

## QA scope completed
- `cd v2/backend && pytest -q tests/test_thesis_plain_english.py tests/test_thesis_engine.py tests/test_thesis_mapper.py tests/test_thesis_response_wiring.py` — 143 passed, 0 failures.

## Behavior change
- `GET /recommendations/` and `/recommendations/jobs/{id}` (via insight cards) now include `thesis_plain_english` and `thesis_v2` per card when scorecard data is available.
- No score math changes, no allocation/deploy changes, no LLM behavior changes, no Supabase SQL changes.

## Frontend notes for next PR
- `thesis_plain_english` is safe to render: no raw metric keys, no raw scores, plain-English strings only.
- Shape is stable: `headline` (str), `quality_label` (str), `valuation_label` (str), `risk_label` (str), `momentum_label` (str), `data_label` (str), `caveats` (list[str]).
- Do not render `thesis_v2` directly — it contains raw scorecard internals (scores, input lists, etc.) that are backend-only.

---

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

## Last change
Deploy allocation table compactness fix: move why under ticker and remove separate WHY column (PR: "fix(deploy): move allocation why text under ticker").

## Files touched
- `v2/frontend/src/app/dashboard/deposits/page.tsx` — Allocation Breakdown table header and row layout updated: removed standalone WHY column, expanded Ticker column, and now renders why text directly under ticker symbol. Added safe subtitle fallback to staging/execution text only when why is missing. No allocation math or role/amount/percent calculations changed.
- `v2/progress_log.md` — concise entry added.
- `docs/ai/HANDOFF.md` — this entry.

## QA scope completed
- `cd v2/frontend && npm run lint` — passed.
- `cd v2/frontend && npm run build` — fails in this environment when Supabase public env vars are unset (`supabaseUrl is required` during prerender).
- `cd v2/frontend && npm test -- --runInBand deposits` — could not run because `jest` binary is unavailable in current environment.

## Behavior change
- Deploy Allocation Breakdown is now more compact: WHY rationale appears beneath ticker in the Ticker cell.
- Repetitive action subtitle no longer appears when why text exists; it is used only as fallback when why is missing.
- No backend changes, no Supabase SQL, no Deploy allocation logic/math changes, no Step 3 persistence changes, no Intel changes.

## Last change
Intel v2: improve thesis_plain_english live card coverage diagnostics and wiring resilience (PR: "fix(intel-v2): harden thesis_plain_english card attachment diagnostics").

## Files touched
- `v2/backend/app/services/recommendation_engine.py` — Added `_build_thesis_fields_for_card(...)` helper to centralize thesis scorecard extraction + translation from `agent_runs.allocation["_thesis_v2"]`; preserves exact-key priority with normalized fallback via existing resolver, and returns lightweight diagnostic codes for deterministic coverage telemetry. `_compute_insight_cards` now uses this helper and logs aggregate `thesis_diag` counts in the existing aggregate completion log.
- `v2/backend/tests/test_recommendation_engine.py` — Added focused tests that verify card thesis attachment behavior for: exact ticker key, safe normalization key mismatch (`BRK-B` vs ` brk.b `), and missing `_thesis_v2` map.
- `docs/ai/HANDOFF.md` — this entry.
- `v2/progress_log.md` — concise entry added.

## Investigation + root cause
- `_thesis_v2` write path exists in the current orchestrator completion path (`allocation_map["_thesis_v2"] = ...`) and is persisted on `agent_runs` completion.
- The card assembly path already reads from `agent_runs.allocation`, but live coverage gaps still occur when run/card linkage or per-card lookup fails quietly.
- Primary deterministic gap class remains ticker/run mapping miss cases (`run_not_found`, missing `_thesis_v2`, or per-ticker key mismatch variants). This PR tightens observability with explicit diagnostic buckets while keeping attachment logic safe and additive.

## Coverage fix type
- Coverage was improved via **lookup/read-path hardening + diagnostics** (no write-path changes needed in this patch).
- Exact ticker key remains preferred; normalized fallback remains conservative and backend-only.

## Explicit no-change confirmations
- No score math changes.
- No LLM behavior or prompts changed.
- No Deploy changes.
- No Supabase SQL or migrations.
- No frontend/UI changes.

## Last change
Intel cards Level-2 rendering/projection hardening (PR: "fix(intel-card): unflatten insufficient-data card display semantics and clean duplicate category rendering").

## Root cause
1. Frontend badge copy rendered raw enum-like conviction labels (`LOW conviction`) and normalized conservative insufficient-data actions to `HOLD`, making all cards look broker-identical even when ticker-specific WHY/RISK/ALT VIEW differed.
2. Frontend subtitle line rendered `{category} · {sector}` without deduplication, so equal values showed twice (`Core · Core`, `ETF · ETF`).
3. Backend insufficient-data `bottom_line` template used the same generic opening phrase across cards ("Interesting setup...") even when trusted/incomplete signal sets differed.

## Fix
- Frontend (`AgentInsightCard`):
  - Conviction badge text now uses plain-English labels: `HIGH -> High confidence`, `MEDIUM -> Moderate confidence`, `LOW -> Evidence limited`.
  - HOLD badge text becomes `WATCHLIST` when `intel_read.insufficient_data=True` to align top posture with conservative action language.
  - Category line now deduplicates equal values and only shows both when distinct.
- Backend (`reasoning_v2_plain_english`):
  - `bottom_line` insufficient-data copy now references trusted + incomplete dimensions directly and removes the repeated generic "Interesting setup" opener.

## Tests
- Backend: `pytest -q backend/tests/test_reasoning_v2_plain_english.py backend/tests/test_intel_read_projection.py` (pass).
- Frontend: added focused rendering-contract tests for conviction copy and category dedupe. Local run blocked in this environment because `jest` binary is unavailable.

## Explicit non-changes
- No Deploy changes.
- No allocation math changes.
- No Business Read re-enable.
- No reasoning_v2 raw internals exposure.
- No raw metric keys exposed.
- No Supabase SQL/migrations.

## Last change
Intel v3 PR 3: backend-only portfolio-level v3 shadow diagnostic summary (PR: "feat(intel-v3-pr3): add portfolio-level v3 shadow summary logging").

## Root cause
PR 2 added per-card v3 shadow diagnostics, but there was no single portfolio-level summary after card assembly. This made it hard to quickly quantify HOLD-collapse exposure and honest-hold coverage across the full recommendation batch.

## Fix
- Added `summarize_shadow_diagnostics()` in `v2/backend/app/services/intelligence/v3/shadow_projection.py` (pure, deterministic helper) to aggregate per-card projection outputs.
- Wired aggregation in `recommendation_engine._compute_insight_cards()`:
  - collect each `_v3_shadow_projection(card)` result (including `None` for fail-soft projection failures),
  - emit one backend debug log after the cards loop with a stable structured summary.
- Summary keys:
  - `schema_version`
  - `total_cards`
  - `projected_cards`
  - `projection_failures`
  - `v2_visible_action_counts`
  - `v3_shadow_action_counts`
  - `hold_collapse_risk_count`
  - `honest_hold_count`
  - `non_hold_shadow_from_v2_hold_count`

## Explicit non-changes
- No visible UI behavior change.
- No API response schema changes.
- No Deploy changes.
- No SQL / Supabase migrations.
- No provider expansion.
- No LLM calls.

## Tests
- Extended `v2/backend/tests/test_v3_shadow_projection.py` with focused PR 3 aggregation tests:
  - empty diagnostics safety,
  - mixed action aggregation + projection failures,
  - v2 HOLD + v3 BUY/TRIM/SELL collapse counting,
  - honest HOLD counted separately from collapse risk.
