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
