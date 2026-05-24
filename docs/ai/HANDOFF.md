# HANDOFF — Current Repo State

Last updated: 2026-05-24 (Stage 9B — Intel Data Foundation Forensics v1 backend diagnostic. New `intel_data_foundation_forensics_v1.py` (pure, read-only): inspects actual persisted research artifacts, positions, and evidence lane outputs to explain, per holding, where the data foundation is missing and why. Per-holding fields: ticker, asset_type, current/target weight available booleans, artifact_exists+status per lane (yfinance_fundamentals, technicals, news_sentiment, sec_companyfacts, sec_catalyst), sec_companyfacts_observation_count (from research_artifact_facts COUNT, never raw payload), sec_companyfacts_reason_not_strong, sec_catalyst_count, valuation_lane_exists (always False — no valuation evidence lane exists at Stage 9B), valuation_inputs_available_summary, etf_fund_composition_artifact_exists (always False — no ETF fund-data provider), crypto_market_context_artifact_exists (uses technical artifact as proxy), thesis_history_exists, root_cause_bucket (deterministic enum, 11 values), next_required_fix. Portfolio aggregates: holdings_by_asset_type, artifacts_existing/usable/strong_by_lane, root_cause_bucket_counts, provider_limited_count, implementation_limited_count, normalization_limited_count. Root cause priority: ETF_PROVIDER_NOT_BUILT → CRYPTO_PROVIDER_NOT_BUILT → ASSET_TYPE_NOT_APPLICABLE → SEC_ARTIFACT_MISSING_WORKER_OR_BACKFILL_GAP → SEC_ARTIFACT_MISSING_CIK_OR_MAPPING_UNKNOWN → SEC_ARTIFACT_EXISTS_BUT_READINESS_WEAK → VALUATION_LANE_NOT_BUILT → NEWS_SENTIMENT_SUPPRESSED_THIN → TARGET_WEIGHT_MODEL_NOT_BUILT → THESIS_HISTORY_NOT_BUILT → DATA_PRESENT_NEEDS_CANONICAL_NORMALIZATION. Supplemental queries: target_allocations (target weight check), recommendations (thesis history proxy), research_artifact_facts COUNT per artifact_id (safe observation counts), portfolio_snapshots (current weight proxy). All fail-soft — errors list captures DB failures. Config: `intel_v3_data_foundation_forensics_enabled` flag (default False). Endpoint: `POST /diagnostics/finance-intel/data-foundation-forensics` (cert-gated + flag-gated; falls back to positions; read-only; 200-ticker cap; hard-locks safe_for_decision=False and synthesis_ready=False). Tests: 78 new in `test_stage9b_data_foundation_forensics.py` (root cause classification, all buckets reachable, artifact exists vs weak, ETF/crypto/valuation classification, policy import regression, leak guard, response shape, portfolio aggregates, fail-soft, observation counts, example fixtures for CRM/NVDA/VTI/SCHD/BTC/XRP). No SQL, no LLM, no providers, no UI, no policy change, no raw payload exposure, safe_for_decision=False throughout. Prior entry: Stage 9A — Coverage & Trust Matrix v1 backend foundation. New `coverage_trust_matrix_v1.py` (pure mapper, no I/O): maps Stage 5J `LaneCoverage` statuses → STRONG/PARTIAL/WEAK/MISSING/NOT_APPLICABLE per 10 research categories. Config: `intel_v3_coverage_trust_matrix_enabled` flag (default False). Endpoint: `POST /diagnostics/finance-intel/coverage-trust-matrix`. 80 tests. No SQL, no LLM, no providers, no UI, no policy change.

## Purpose

This file is **current operational state**, not a historical log. It is meant to be loaded into context every session, so it must stay compact. Do not append PR-by-PR history. When something changes, replace or summarize the affected section instead of adding new entries.

## Current product stage

- Roadmap stage: **Stage 3G** (Alert Center UI v1). Stage 3F merged PR #355 — Railway activation config for email delivery worker (PROCESS_TYPE=email_delivery). Stage 3E merged PR #354 (SQL 022 applied). Stage 3D merged PR #353 (SQL 021 applied). Stage 3C merged PR #352.
- Stage 3G summary (current — branch `claude/alert-center-ui-v1-cAzJ8`): Read-only Alert Center UI reachable at `/dashboard/alerts`. Shows alert candidates and delivery outbox from existing backend endpoints (`GET /api/v1/alert-candidates`, `GET /api/v1/alert-delivery-outbox`). Navigation item "Alerts" added to BottomNav and SideNav. Dry-run safety banner always visible. Plain-English status labels, severity pills, empty/loading/error states. Pure mapping functions extracted to `src/lib/alert-center.ts` with 28 unit tests. No new SQL, no email delivery changes, no Watchtower/Intel v3 changes.
- Stage 3E summary (merged PR #354): Resend email delivery worker processing pending `alert_delivery_outbox` rows. Env-gated (default OFF, default dry-run ON). Files: `resend_client_v1.py`, `alert_email_delivery_worker_v1.py` (claim-before-send, fail-soft), `alert_email_delivery_worker_entrypoint.py`. Outbox service methods: `fetch_pending_email_rows`, `claim_for_delivery`, `mark_sent`, `mark_failed`. Config: `ALERT_EMAIL_DELIVERY_ENABLED` (default false), `ALERT_EMAIL_PROVIDER`, `RESEND_API_KEY`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`, `ALERT_EMAIL_DRY_RUN` (default true). SQL 022 applied. 38 tests. Structured log: `alert_email_delivery_summary scanned=... sent=... failed=... skipped=... status_update_failed=... dry_run=... provider=resend`.
- Stage 3F Railway activation: Use `PROCESS_TYPE=email_delivery` on a separate Railway service. Do NOT wire into Watchtower. See entrypoint docstring for step-by-step dry-run and real-send instructions.
- Stage 3D summary (merged PR #353): Provider-neutral alert delivery outbox. SQL migration 021 (`alert_delivery_outbox` table — **applied**). `alert_delivery_policy_v1.py` (pure, no IO), `alert_delivery_outbox_service.py` (idempotent persistence + 24h noisy-repeat suppression, exact-dedupe-before-suppression ordering), `GET /api/v1/alert-delivery-outbox` (read-only, authenticated). Fail-soft Step 5 in `watchtower_alert_candidate_hook_v1.py` — outbox attempted for ALL returned candidate rows (created + deduped) for self-healing. No external delivery, no provider SDKs, no LLM calls, no frontend UI. 109 tests pass.
- Stage 3C summary (merged PR #352): `watchtower_alert_candidate_hook_v1.py` wires candidate generation after certified Intel v3 snapshot publishes. Hook injected into `compare_and_republish()` and `republish_after_analyst_eligibility()`. Fail-soft. 23 tests pass. No SQL, no delivery, no UI.
- Stage 3B summary (merged PR #350): Pure deterministic policy module `alert_trigger_policy_v1.py` + `AlertCandidateService` + `watchtower_alert_candidates` table (SQL migration 020 — **applied**) + `GET /api/v1/alert-candidates` (read-only, authenticated). 79 tests pass. Evidence band `_ACTIONABLE_BANDS = {"STRONG","PARTIAL"}` — PARTIAL is the serialized label for AxisBand.OK. Feedback suppression: executed (indefinite), ignored/not_relevant/too_risky (7d), snoozed (14d default or `cooldown_until`). `action_feedback_events.cooldown_until` column added via ALTER TABLE in migration 020.
- Stage 3A summary (merged PR #349): `action_feedback_events` table, service, router (`POST /api/v1/action-feedback`, `GET /api/v1/action-feedback`). SQL migration 019. 22 tests pass.
- Current north-star reminder: Intel → Deploy → Watchtower; deterministic backend policy owns visible Buy/Hold/Trim/Sell authority. See `docs/product/NORTH_STAR.md`.

## Current architecture — Build 2 additions

**Build 2: Evidence-grade certification + publish contract** (PR #pending). After Watchtower writes fresh price evidence to `portfolio_snapshots`, the visible Intel v3 snapshot is now automatically re-certified from that evidence without analyst LLM jobs.

New module: `watchtower_intel_republisher_v1.py`
- `compare_and_republish(user_id, client, *, intel_republish_callable)` — compares `intel_v3_snapshots.payload.generated_at` vs `portfolio_snapshots.snapshot_at`. If evidence is newer (>10s threshold), calls `intel_republish_callable(user_id)` which wraps `IntelV3Service.run_prewarm_snapshot()`. Zero LLM calls; analyst_jobs_queued=0 always.
- `get_evidence_freshness_state(user_id, client, *, intel_snapshot_generated_at)` — lightweight comparison for API response embedding.
- `PUBLISH_*` constants: `certified_current` | `rebuilt_and_published` | `republish_pending` | `certification_blocked` | `no_snapshot_exists`

Extended modules:
- `watchtower_callables_v1.py` — adds `build_default_intel_republish_callable()`, which wraps `IntelV3Service.run_prewarm_snapshot(skip_persist_on_fail=True)` (deferred import preserves boundary; `skip_persist_on_fail=True` prevents failed Watchtower-triggered rebuilds from overwriting a prior `worker_certified` snapshot).
- `watchtower_background_refresh_worker_v1.py` — `WatchtowerBackgroundRefreshWorker` now accepts `intel_republish_callable`. After `persist_watchtower_price_snapshot()` succeeds, calls `compare_and_republish()`. `WatchtowerRefreshCycleResult` carries `intel_republish_result` dict.
- `watchtower_worker_entrypoint.py` — wires `build_default_intel_republish_callable()` in the background loop.
- `intel_v3_service.py` — `get_latest_snapshot()` embeds `evidence_freshness_state` in the API response (non-mutating copy). `enqueue_run_v3()` urgent path now passes `intel_republish_callable` into `run_watchtower_cycle_for_user()`. `run_prewarm_snapshot()` has `skip_persist_on_fail=False` param — when `True` and certification fails, skips `_persist_snapshot()` to preserve the prior `worker_certified` active snapshot.

**compare_and_republish() result semantics (post-patch):** After calling `intel_republish_callable(user_id)`, inspects `returned_payload["snapshot_source"]`. Only `"worker_certified"` → `PUBLISH_REBUILT_AND_PUBLISHED`; `"certification_failed"` or any other value → `PUBLISH_CERTIFICATION_BLOCKED` with source in error field.

**get_evidence_freshness_state() error behavior (post-patch):** DB errors return `PUBLISH_REPUBLISH_PENDING` (honest non-green) — not `certified_current`. The portfolio snapshot DB call is inlined (not delegated to the error-swallowing helper) so errors propagate correctly.

Boundary preserved: `watchtower_background_refresh_worker_v1.py` does NOT import `decide()`. The republish callable is injected, built by `watchtower_callables_v1.py`.

43 tests in `test_watchtower_build_2.py` (28 Build 2 + 15 patch). 91 Build 1D tests still pass.

Key structured logs to confirm in production:
- `watchtower_intel_republisher.publish_decision user_id=... publish_status=rebuilt_and_published evidence_newer_than_certified_snapshot=True analyst_jobs_queued=0`
- `intel_v3_snapshot_response_summary ... evidence_freshness_state=certified_current` (after republish completes)
- `intel_v3_worker_certified_snapshot_published` (from `run_prewarm_snapshot` inside the callable)

## Current architecture / runtime state

- OS v4 is the canonical operating system. No v4.2 or v5 labels.
- Visible decision authority is owned by the deterministic Intel v3 backend policy. LLMs / agents / research workers cannot own final visible action authority.
- **Intel v3 all-or-nothing certified intelligence run contract (Stage 3.3).** `POST /intel/v3/run` (Run Intel button) now calls `service.enqueue_run_v3()` — it enqueues background jobs and returns `{status: "refresh_requested"}` immediately. It does NOT build a snapshot or call `decide()`. `GET /intel/v3/snapshot` (page load) returns the latest persisted snapshot with no LLM calls. After the worker completes a full run, `run_prewarm_snapshot()` calls `check_certified_intel_run_contract()` — a pure async read-only validator that checks all 10 conditions per holding (active recommendation, agent_run_id, matching agent_insight by run_id, agent_run completed, analyst_verdict fields non-empty non-template, freshness within SLA). Only if ALL active holdings pass does the snapshot get `snapshot_source="worker_certified"`; otherwise `"certification_failed"`. The frontend polls every 15s after clicking Run Intel until `snapshot_source=worker_certified` or 5-minute timeout. Green banner is shown ONLY when `snapshot_source=worker_certified` AND `certified_holding_count === total_holding_count`. Six UI states: `certified_current` (green), `latest_certified_new_refresh_running` (amber), `refreshing_analyst_intelligence` (grey), `blocked_certification_failed` (red), `unavailable_refresh_failed` (red), `unavailable_evidence_incomplete` (grey). Structured logs: `intel_v3_certified_contract_summary`, `intel_v3_run_request_received`, `intel_v3_full_refresh_enqueued`, `intel_v3_worker_certified_snapshot_published`, `intel_v3_worker_certified_snapshot_rejected`, `intel_v3_ui_status_summary`. Background worker still: `analyst_refresh_worker_v1.AnalystRefreshWorker` → `FullPortfolioAnalystRefreshAdapter` → `AgentOrchestrator` → `analyst_evidence_writer_v1` → `prewarm_intel_v3_snapshot()`. For broader architecture see Stage 3.1–3.2c notes below.
- For long architecture references, read `artifacts/Intel_v3_Architecture_Plan_Draft2_*`, `artifacts/Intel_v3_Architecture_Plan_Draft3_*`, and `artifacts/Intel_v3_Living_Cockpit_Status_Reconciliation_*` rather than copying them here.
- Runtime workflow guardrails: advisory `.claude/hooks/ai_os_advisory.py` reminds about contract / claim-safety / SQL / env paths. No blocking hooks.

## Stage 8C PR 2.5 — Post-lane Stage 5J/5K readiness trigger (current PR, open)

**Root cause:** Stage 5J/5K readiness logs never appeared after Run Intel because:
1. The orchestrator gated Stage 5J behind `intel_v3_evidence_coverage_dispatch_log_enabled` (default False).
2. Stage 5K (`compute_decision_input_readiness`) was never called from the orchestrator path.
3. `snapshot_sentiment_readiness` was only emitted during snapshot building — skipped when the republisher returned `skipped_no_new_evidence` (existing SEC catalyst v2 artifacts predate the current snapshot timestamp).

**Fix (one file):** `intel_v3_evidence_lane_orchestrator_v1.py` — replaced the flag-gated Stage 5J-only block with an unconditional post-lane Stage 5J + 5K evaluation:
- Calls `compute_research_evidence_coverage` (Stage 5J) → emits `sec_catalyst_stage5j_readiness` per ticker.
- Calls `compute_decision_input_readiness` (Stage 5K) → emits `sentiment_stage5k_source_selection` per ticker.
- Emits `snapshot_sentiment_readiness` for usable sec_catalyst_sentiment lanes.
- Reads the full `is_active=True` artifact set — idempotency-skipped existing artifacts are valid evidence inputs.
- Fail-soft; never raises into the orchestrator path.
- Runs after evidence lane completion, before the republisher check, so diagnostics appear regardless of republisher decision.

**Tests:** 29 new backend tests in `test_stage8c2_5_post_lane_readiness.py` (structural source proofs, idempotency cases, Stage 5J LIMITED, Stage 5K source selection, log emission, ETF/crypto skip, no policy mutations). No SQL, no env vars, no providers, no LLM, no UI, no decision policy changes.

**Expected Railway logs after merge (keep INTEL_V3_SENTIMENT_CATALYST_EVIDENCE_ENABLED=true):**
- `sec_catalyst_stage5j_readiness ticker=<t> status=LIMITED is_usable=True artifact_id=<uuid>`
- `sentiment_stage5k_source_selection ticker=<t> selected=sec_catalyst_sentiment suppressed_editorial_present=<bool>`
- `snapshot_sentiment_readiness ticker=<t> status=LIMITED source=sec_catalyst_sentiment`
- Editorial/yfinance sentiment stays suppressed (not selected over usable SEC catalyst).
- ETF/BTC/XRP conservative skip behavior unchanged.

## Stage 8C PR 2.4 — Certify SEC catalyst sentiment propagation into Stage 5J/5K and snapshot (merged PR #402)

**Fix:** Stage 5J: added `artifact_id` to `sec_catalyst_stage5j_readiness` log. Stage 5K: added `_log_sentiment_source_selection()`. `intel_v3_service.py` (both paths): added `snapshot_sentiment_readiness` log when sentiment is usable. 17 new backend tests. No SQL.

## Stage 8C PR 2.3 — SEC catalyst idempotency + lane isolation fix (merged PR #401)

**Root cause:** `SEC_CATALYST_MODEL_VERSION` was `.v1` — same idempotency key as pre-PR400 THIN artifacts. Idempotency check skipped the write.

**Fix:** Bumped to `sec_catalyst_sentiment_adapter.v2`; added skill_pack/model_version to write_ok/idempotency_skip/clean_replacement logs. 9 new tests.

## Stage 8C PR 2 runtime fix — schema-valid fact_kind (PR #399, open)

**Root cause:** `FactRecord.fact_kind="sec_catalyst_event"` violated `research_artifact_facts_fact_kind_check` (Supabase error 23514). Every MSFT/CRM/WMT/COST/QCOM catalyst write failed; `sentiment_catalyst_evidence_complete artifact_id=none reason=service_write_failed`.

**Fix:** `fact_kind="catalyst_item"` — existing schema-valid value; `axis_hint="catalyst"` and all structured_payload fields preserved. 1-line change + 28-line test (`test_fact_kind_is_schema_valid_catalyst_item`). No SQL.

## Stage 8C PR 2 — SEC Catalyst Sentiment Evidence Lane (merged PR #398)

**Before:** `sentiment_event_adapter_v2.py` existed but no real free source produced LIMITED/READY sentiment artifacts.

**After:** Flag-gated SEC/company catalyst evidence lane (`INTEL_V3_SENTIMENT_CATALYST_EVIDENCE_ENABLED`, default OFF). Writes honest `COMPANY_AUTHORED`/`PRIMARY_AUTHORITY` `sentiment_event` artifacts from SEC EDGAR filing metadata (10-K, 10-Q, 8-K). `sec_catalyst_sentiment` lane added to Stage 5J registry.

**Key components:**
- `sec_catalyst_sentiment_adapter_v1.py` — pure adapter: `SecEdgarProviderResult` → `WorkerOutput` with `artifact_type=sentiment_event`, `skill_pack=sec_catalyst_sentiment_evidence_v1`. Deterministic form_type→category/materiality/freshness mapping. No polarity.
- `_FORM_ATTRIBUTES` map — 10-K → earnings/HIGH/COMPLETE/180d, 10-Q → earnings/MEDIUM/PARTIAL/90d, 8-K → corporate_action/HIGH/PARTIAL/30d.
- `run_sec_catalyst_sentiment_evidence()` — runner in `evidence_lane_runner_v1.py`, reusing existing `sec_edgar_provider`. Equity-only guard via `classify_sec_metric_candidate`. Structured logs: `sentiment_catalyst_evidence_start`, `sentiment_catalyst_evidence_complete`, `sentiment_catalyst_evidence_skipped`.
- `LANE_SEC_CATALYST_SENTIMENT` — added to `TICKER_LANE_REGISTRY` in `research_evidence_coverage_read_model_v1.py`.
- Config: `intel_v3_sentiment_catalyst_evidence_enabled: bool = False`.

**Tests:** 49 new tests in `test_stage8c2_sec_catalyst_sentiment.py`. Existing 333 related tests pass. No SQL, no LLM, no new paid provider, no UI, no policy changes.

**Runtime validation:** After enabling flag in Railway, look for `sentiment_catalyst_evidence_complete` log key. Do not claim production success until at least one real eligible equity artifact or an honest all-skipped result appears in logs.

## Stage 8C PR 1 — Sentiment Event v2 Provider-Agnostic Adapter (merged)

`sentiment_event_adapter_v2.py` — provider-agnostic adapter normalizing SEC/company catalyst or vendor inputs into NOT_USABLE/LIMITED/READY/INELIGIBLE without adding a provider or changing decisions. Editorial promotion guard, ticker_match_confidence cap, catalyst_category/materiality/ticker_match normalization, dedupe key, ineligible-asset guard (crypto/ETF), safe URL filter. 76 new backend tests.

## Stage 8B — Sentiment Evidence Quality Threshold (merged PR #396)

**Root cause investigation:** Sentiment artifacts are ALWAYS SUPPRESSED_INCOMPLETE because yfinance news sources are assigned `EDITORIAL_CONTEXT` authority, which is hard-capped to `THIN` completeness band, which triggers `SUPPRESSED_INCOMPLETE` in the truth adapter. This is CORRECT behavior — editorial context is not decision-useful.

**Gap fixed:** No explicit, auditable quality criteria existed for when sentiment could graduate to LIMITED/READY. The suppression was implicit in the `EDITORIAL_CONTEXT → THIN` cap.

**Fix:**
1. `sentiment_quality_threshold_v1.py` (new) — Explicit quality gate. Defines `evaluate_sentiment_quality()` with five deterministic criteria: freshness=FRESH, source authority NOT in `{EDITORIAL_CONTEXT, UNKNOWN}`, completeness NOT in `{THIN, NOT_EVALUABLE}`, not contradicted, at least one source+fact. Returns `NOT_USABLE` (with reason codes) or `LIMITED`/`READY`. Exports `SENTINEL_EDITORIAL_CONTEXT_REASON` constant.
2. `research_evidence_coverage_read_model_v1.py` (Stage 5J) — New `_classify_sentiment_status()` function imported in `_build_lane_coverage()` when `lane == LANE_NEWS_SENTIMENT`. Sub-classifies SUPPRESSED reasons: `editorial_context_present_not_decision_useful` (SUPPRESSED_INCOMPLETE with EDITORIAL/UNKNOWN authority — correct by design) vs `suppressed_data_quality_issue` (other suppressions like contradictions).
3. `intel-v3-explanation.ts` (frontend) — Updated MISSING sentiment copy from "thin or not available" → "not yet available for this ticker." to cleanly distinguish from INSUFFICIENT ("available but not yet strong enough").

**Quality path confirmed:** USABLE_WITH_LIMITATIONS artifacts → STATUS_LIMITED in Stage 5J → READINESS_LIMITED in Stage 5K → `sentiment_status="LIMITED"` in snapshot → "Some news and sentiment data is available." in frontend. No code change needed for propagation — the path already existed.

**Tests:** 36 new backend tests in `test_stage8b_sentiment_quality_threshold.py` covering: quality threshold criteria, Stage 5J sub-reasons, Stage 5K propagation (SUPPRESSED→INSUFFICIENT, MISSING→MISSING, LIMITED→LIMITED, READY→READY), crypto guardrails, non-sentiment lanes unaffected. 1 frontend test updated.

## Stage 8A.3 — Post-evidence-lane deterministic snapshot republish (merged PR #395)

**Root cause:** `enqueue_run_v3()` dispatches evidence lanes fire-and-forget via `asyncio.create_task`. After lanes write fresh technical artifacts to `research_artifacts`, nothing triggers snapshot republish — the Watchtower republisher's `compare_and_republish()` compares `portfolio_snapshots.snapshot_at` (price evidence timestamp), not `research_artifacts.generated_at`. So the certified snapshot stayed stale and the drawer regressed to legacy fallback.

**Fix:**
1. `watchtower_intel_republisher_v1.py` — new `compare_and_republish_after_evidence_lanes(user_id, client, *, intel_republish_callable)` queries `research_artifacts` for usable (`is_usable=True`) technical_signal artifacts per ticker and triggers republish if any are newer than the current snapshot by `_EVIDENCE_NEWER_THRESHOLD_SECONDS=10`. Emits `intel_v3_post_lane_republish_check` log. Idempotent: after republish, snapshot `generated_at` is NOW so pre-existing artifacts will be older on the next check → `skipped_no_new_evidence`.
2. `intel_v3_service.py` — `_run_evidence_lanes_safe()` closure inside `enqueue_run_v3()` calls `compare_and_republish_after_evidence_lanes()` after successful lane completion. Failures are caught and logged as `intel_v3_post_lane_republish_failed` — lane failures skip the republish entirely.

**After fix:** MSFT technical evidence completed FRESH after Run Intel → post-lane republish fires → certified snapshot rebuilt with `technical_signals_status=LIMITED` → drawer shows "Some market and price behavior data is available" instead of legacy placeholder. BTC/XRP conservative/blocked behavior preserved.

**Tests:** 14 new backend tests in `test_stage8a3_post_lane_republish.py`; 12 new frontend tests in `intel-v3-drawer-clarity.test.ts`; no SQL, no providers, no LLM, no decision policy changes.

## Stage 8 / 8A.2 — Technical evidence propagation (merged PRs #393, #394)

- **Stage 8A.2 (PR #394):** `watchtower_evidence_collector_v1` queries `research_artifacts` for usable technical_signal artifacts per ticker. `intel_v3_service` always computes Stage 5J/5K shadow; `snapshot_builder` patches `technical_signals_status` from `research_axis_readiness` when Stage 6 off. 18 new tests.
- **Stage 8 (PR #393):** Bumped `_TECHNICALS_MODEL_VERSION` v1 → v2 to force supersession of stale SUPPRESSED_UNKNOWN_SOURCE artifacts. Fixed `buildIncompleteEvidenceSentences` to distinguish INSUFFICIENT/STALE_OR_UNKNOWN (present but thin) from MISSING (no artifact). 6 backend + 19 frontend tests.

## Stage 7 Plain-English Intelligence Surface (PRs #388, #389, #391, #392 merged)

**Stage 7C (merged PR #392):** `_build_synthetic_evidence_explanation()` in `snapshot_builder.py` derives structured `evidence_explanation` from `evidence_quality` band when Stage 6 off. `STAGE7_EXPLANATION_CONTRACT_VERSION` → `stage7_explanation_v2`. Old snapshots with `evidence_explanation=null` trigger deterministic recertification. 49 backend + 93 frontend tests.

**Stage 7B (merged PR #391):** 7 decision-specific drawer sections, `onceOnly()` dedup, `buildSupportingEvidenceSentences()`, `buildIncompleteEvidenceSentences()`, `buildWhyActionExplanation()`, `deduplicateTexts()`. All 5 ComingLaterPanel blocks removed.

**Stages 7/7A (merged PRs #388, #389):** `_build_evidence_explanation()` from Stage 6 governance result; `stage7_snapshot_contract_v1.py` three-gate freshness guard; translation layer (`readinessToDisplay`, `governancePriorityToExplanation`, `convictionCapLabel`, `buildEvidenceLaneRows`, `buildSafetyDisplay`).

**Stage 6 (merged):** `intel_v3_evidence_aware_governance_v1.py`. Flags: `INTEL_V3_EVIDENCE_AWARE_POLICY_ENABLED` (default False). When enabled, per-ticker governance result replaces synthetic explanation with real per-axis readiness.

**Production next steps (in order):**
1. Merge Stage 8 PR (#393) — technicals artifacts re-enriched on next evidence run; INSUFFICIENT wording corrected immediately.
2. Enable evidence lanes: `INTEL_V3_FUNDAMENTALS_EVIDENCE_ENABLED=true`, `INTEL_V3_TECHNICALS_EVIDENCE_ENABLED=true`, `INTEL_V3_NEWS_SENTIMENT_EVIDENCE_ENABLED=true`, `INTEL_V3_SEC_COMPANYFACTS_EVIDENCE_ENABLED=true` + `SEC_EDGAR_USER_AGENT`. Keep `INTEL_V3_RESEARCH_WORKERS_ENABLED=true`.
3. Run `POST /intel/v3/run` to populate evidence lanes.
4. Enable `INTEL_V3_EVIDENCE_AWARE_POLICY_ENABLED=true` — Stage 6 governance result replaces synthetic explanation with real per-axis readiness (READY/LIMITED/MISSING per lane).

## Previous evidence-readiness bridge (Stage 5K)

**Stage 5K (merged):** Research Evidence Decision Input Adapter v1 — shadow-only, backend-only. Maps Stage 5J coverage lanes to four Intel v3 axis readiness signals: `company_fundamentals`, `technical_signals`, `sentiment`, `macro_context`. Readiness values: READY | LIMITED | INSUFFICIENT | MISSING | NOT_APPLICABLE. ETF/crypto: `sec_lane_applicable=False`, never penalized. `safe_for_decision=False` and `shadow_only=True` immutable. 39 tests. No SQL, no UI, no LLM, no providers, no decision changes.

## Previous evidence-readiness bridge (Stage 5J)

**Stage 5J (current PR):** Research Evidence Coverage Read Model v1 — deterministic, read-only summary over previously-written Stage 5A–5I research artifacts.

**What landed in Stage 5J:**
- `research_evidence_coverage_read_model_v1.py` (new) — `compute_research_evidence_coverage(user_id, tickers, db_client) -> ResearchEvidenceCoverageSummary`. Pure read. Per-ticker lanes: `sec_company_facts` (fundamental_quality + sec_companyfacts_evidence_v1), `fundamentals` (fundamental_quality + fundamentals_evidence_v1), `technicals` (technical_signal + technicals_evidence_v1), `news_sentiment` (sentiment_event + news_sentiment_evidence_v1). Portfolio-scope lane: `macro_context` (portfolio_exposure + fred_macro_evidence_v1). Coverage status set per (lane, ticker): READY / LIMITED / SUPPRESSED / NOT_EVALUABLE / STALE_OR_UNKNOWN / MISSING. Reads `truth_usability_assessment`, `source_credibility_assessment`, `contradiction_assessment`, `evidence_completeness_assessment` from artifact payload but never re-emits raw payload, source URLs, fact contents, or API keys. Defensive: picks latest active by `generated_at` if duplicates ever exist. Fail-soft on DB error.
- `intel_v3_evidence_lane_orchestrator_v1.py` (modified) — after dispatch, when `intel_v3_evidence_coverage_dispatch_log_enabled=true`, emits one compact `research_evidence_coverage_summary` log via `log_coverage_summary()`. Fail-soft; default off.
- `routers/diagnostics.py` (modified) — `POST /diagnostics/finance-intel/research-evidence-coverage` (cert-gated + `INTEL_V3_EVIDENCE_COVERAGE_DIAGNOSTICS_ENABLED=true`). Falls back to positions when no tickers supplied. Capped at 200 tickers per request. Read-only; never triggers an evidence run.
- `config.py` (modified) — two new flags: `intel_v3_evidence_coverage_diagnostics_enabled` (default False) and `intel_v3_evidence_coverage_dispatch_log_enabled` (default False).
- `test_stage5j_evidence_coverage_read_model.py` (new) — 14 tests: usable SEC counted per ticker; usable FRED macro counted at portfolio level; missing lanes honest; suppressed excluded from ready; duplicates pick latest active; read-only (no inserts/updates); no payload/secret leakage; safe_for_decision=False; USABLE_WITH_LIMITATIONS → LIMITED; STALE freshness overrides; NOT_EVALUABLE; ticker normalization/dedup; DB error fail-soft; compact log no payload leak.

**Providers actually called**: none (read-only). **SQL required**: NO (no schema change). **UI**: none. **LLM**: none. **safe_for_decision**: False. **Page-load**: never (`GET /intel/v3/snapshot` does not call this). **Visible decision changes**: none.

**Validation:** 380 stage 5A/5E/5H/5H.1/5H.2/5H.3/5I/5J tests pass.

**Next stage:** Stage 5K (see above) has now consumed Stage 5J as its input — complete.

## Previous evidence lane production wiring status (Stage 5I)

**Stage 5I (merged):** FRED Official Macro Evidence Lane v1.

**What changed in Stage 5I:**
- `fred_provider_v1.py` (new) — typed, deterministic, sync FRED API client. Bounded per-session request budget; two requests per series (metadata + recent observations). Honest fail-closed on no_api_key / timeout / rate_limit / malformed / no_observations. Allowlisted series only: `FEDFUNDS`, `DFF`, `DGS10`, `DGS2`, `T10Y2Y`, `CPIAUCSL`, `UNRATE`, `PAYEMS`, `GDP`, `GDPC1`. Never raises.
- `fred_macro_adapter_v1.py` (new) — pure, no-IO adapter. Converts `FredProviderResult` → portfolio-scope `WorkerOutput`. artifact_type=`portfolio_exposure` (existing DB enum; TODO documented to extend to `macro_context` in a future SQL migration), skill_pack=`fred_macro_evidence_v1`, model_version=`fred_official_macro_v1`. One `SourceRecord` per FRED series (provider_name=`fred`, source_url=`https://fred.stlouisfed.org/series/<id>`, source_kind=`other`). One `FactRecord` per observation, preserving `series_id`, `metric_label`, `value`, `unit`, `frequency`, `observation_date`, `realtime_start/end`, `fred_category`, `fred_last_updated`. Confidence band from successful-series count; freshness from latest observation date.
- `evidence_lane_runner_v1.py` (modified) — added `_is_fred_macro_enabled()` + `run_fred_macro_evidence()` (portfolio-scope, ticker-agnostic; one artifact per explicit run). Compact logs: `fred_macro_evidence_start`, `fred_macro_series_fetched`, `fred_macro_series_skip`, `fred_macro_evidence_complete`. Router-consulted (must resolve to FRED FREE/OFFICIAL).
- `evidence_provider_registry_v1.py` (modified) — FRED flipped to `default_enabled=True` with `requires_api_key=True`. priority=2 (above yfinance, below sec_edgar).
- `intel_v3_evidence_lane_orchestrator_v1.py` (modified) — runs FRED macro lane once per explicit dispatch (not per ticker). Fail-soft: macro failure does not break per-ticker dispatch. Empty-ticker early-return removed so macro can still fire on empty portfolios. `macro_artifact_id` added to dispatch-complete log.
- `config.py` (modified) — new flags `intel_v3_macro_evidence_enabled: bool = False` and `fred_api_key: Optional[str] = None`.
- `test_stage5i_fred_macro_evidence.py` (new) — 80 tests: registry/router, provider client, adapter, WorkerOutput builder, runner integration through `ResearchArtifactServiceV1`, orchestrator wiring, safety/boundary invariants.

**Providers actually called**: fred (Stage 5I) when flag on + api key set; sec_edgar (Stage 5H), yfinance (Stage 5F) — unchanged. Paid providers remain disabled.
**SQL required**: NO. Used least-misleading existing `portfolio_exposure` artifact_type and existing `other` source_kind (TODO in adapter notes the future `macro_context` artifact_type + dedicated macro source_kind migration).
**UI changes**: No.
**Per-run cost**: at most 2 HTTP requests × 10 allowlisted series = 20 FRED requests per explicit Intel v3 run. Macro lane runs only on explicit `POST /intel/v3/run`, never on page load.
**safe_for_decision**: stays False. Macro evidence is portfolio context, never visible Buy/Hold/Trim/Sell authority.
**Stage 5I.1 patch (current PR):** Production activation of Stage 5I revealed two issues:
1. `research_artifact_facts` inserts failed HTTP 400 on `research_artifact_facts_axis_hint_check` because Stage 5I emitted `axis_hint="macro"` while migration 017's CHECK constraint allows only `{evidence, risk, price, quality, catalyst, exposure}` or NULL. Result: artifact wrote, sources wrote, every fact insert failed, `fred_macro_evidence_complete artifact_id=none reason=service_write_failed`.
2. Railway logs showed live `FRED_API_KEY` inside httpx `HTTP Request: GET …?api_key=…` lines.

Fixes in `fred_macro_adapter_v1.py`:
- `axis_hint=None` for all FRED FactRecords (writer already only sets the column when truthy). Macro identity preserved in `structured_payload`: `provider="fred"`, `macro_category`, `series_id`, `metric_name`, `observation_date`, `lane="macro"`. skill_pack `fred_macro_evidence_v1` remains the lane discriminator. No SQL.

Fixes in `fred_provider_v1.py`:
- Added `_ApiKeyRedactingFilter` (regex `api_key=…` → `api_key=[REDACTED]`) installed at module import time on `httpx`, `httpcore`, and this module's loggers. `httpx`/`httpcore` log level raised to WARNING to suppress the request-URL INFO line entirely. High-level runner logs (`fred_macro_series_fetched series_id=… observation_count=… latest_date=…`) are unchanged.

Tests: existing `test_fact_axis_hint_is_macro` replaced with `test_fact_axis_hint_is_db_valid` + `test_fact_payload_preserves_macro_identity`. Added `TestFredMacroDbAxisHintConstraint` (worker-output, runner-persisted, full artifact+sources+facts written, usability label becomes `USABLE` / `USABLE_WITH_LIMITATIONS`) and `TestFredApiKeyLogRedaction` (filter scrubs msg + args, httpx/httpcore filter installed at level ≥ WARNING, end-to-end runner caplog contains no key). 103 Stage 5I tests pass; Stage 5B/5E/5F/5G/5H/5H2/5H3 suites still pass.

**Next runtime validation:** rotate FRED key (the old leaked one), set the new `FRED_API_KEY` in Railway, keep `INTEL_V3_MACRO_EVIDENCE_ENABLED=true`, run `POST /intel/v3/run`, confirm in Railway logs:
- `fred_macro_evidence_start series_count=10 worker_run_id=...`
- `fred_macro_series_fetched series_id=DGS10 observation_count=12 latest_date=2026-05-...`
- `fred_macro_evidence_complete series_attempted=10 series_written=N artifact_id=<uuid>` (UUID, not `none`)
- `fred_macro_usability_summary observation_count=N strongest_authority=PRIMARY_AUTHORITY ... usability_label=USABLE|USABLE_WITH_LIMITATIONS provider_aware_override_count=N`
- `intel_v3_evidence_lanes_dispatch_complete ... macro_artifact_id=<uuid>`
- Exactly one new active `portfolio_exposure` row with `skill_pack='fred_macro_evidence_v1'` per explicit run.
- No `api_key=…` strings anywhere in Railway logs.

## Previous evidence lane wiring status (Stage 5H.3)

**Stage 5H.3 fix (merged):** SEC CompanyFacts contradiction grouping and non-equity ticker eligibility guard.

**Root cause of remaining false SUPPRESSED_CONTRADICTED after PR #378:** the generic contradiction detector groups by `(claim_key, fact_kind, period, as_of)` only. For SEC XBRL `metric_observation` facts that grouping is too coarse — it ignores `unit`, `fiscal_year`, `fiscal_period`, `period_start`, `period_end`, and `frame` structured-payload fields. Any combination of those that hashes into the same coarse group can produce a false contradiction when the parser legitimately keeps distinct XBRL observations (e.g., instant balance-sheet values with no `period_start`, or quarterly vs YTD durations under unusual `fy/fp` shapes). Separately, BTC and XRP were being mapped to unrelated SEC companies by ticker-symbol collision because no instrument guard ran before SEC EDGAR lookup.

**Fix:**
- `contradiction_detector_v1` adds a SEC-specific group key for `metric_observation` facts whose `structured_payload.provider == "sec_edgar"`. The key includes `provider + metric_name + unit + fiscal_year + fiscal_period + period_start + period_end + frame + filed`. Different metrics, units, fiscal periods, durations, or filings cannot collide. `accession_number` is intentionally excluded so two filings asserting different values for the same identity (restatement) still flag as a true contradiction.
- `evidence_lane_runner_v1.run_sec_companyfacts_evidence()` runs `sec_metric_candidate_classifier.classify_sec_metric_candidate(ticker, category)` before any SEC lookup, using `holding_context` (`category` / `asset_type` / `security_type` / `instrument_type` / `asset_class`) when present. ETFs/funds/crypto are skipped — no provider call, no artifact, no fabricated SEC identity. Conservative fallback skips known portfolio crypto symbols (BTC, XRP) and ETFs when metadata is missing.
- New structured log `sec_companyfacts_usability_summary ticker=... observation_count=... contradiction_count=... usability_label=... sample_group_keys=...` for runtime diagnosis. Plus `sec_companyfacts_skip_non_equity ticker=... classification=... category=... reason_codes=...` when the guard skips a ticker.

**Next runtime validation:** Re-run Intel v3 (`POST /intel/v3/run`) and confirm in Railway:
- SEC CompanyFacts written artifacts are mostly `usability_label=USABLE` or `USABLE_WITH_LIMITATIONS` (not `SUPPRESSED_CONTRADICTED`).
- `sec_companyfacts_skip_non_equity ticker=BTC` and `ticker=XRP` appear; no SEC artifact rows are written for BTC/XRP.

**Stage 5H.1 background:** `POST /intel/v3/run` dispatches all enabled evidence lanes via `run_enabled_evidence_lanes_for_portfolio()`. Fire-and-forget. Page-load contract preserved.

**To confirm in Railway after enabling flags:**
- `intel_v3_evidence_lanes_dispatch_start total_tickers=N user_id=... parent_intel_run_id=...`
- `sec_companyfacts_artifact_written ticker=... observation_count=N tag_count=N confidence=... freshness=...`
- `sec_companyfacts_skip_no_artifact ticker=... reason=no_cik/no_observations` (for ETFs/crypto)
- `intel_v3_evidence_lanes_dispatch_complete tickers_attempted=N artifacts_written=N skipped=N`

**Flags required:**
- `INTEL_V3_RESEARCH_WORKERS_ENABLED=true` (global kill switch)
- `INTEL_V3_SEC_COMPANYFACTS_EVIDENCE_ENABLED=true` + `SEC_EDGAR_USER_AGENT=<agent>` for SEC lane
- `INTEL_V3_MACRO_EVIDENCE_ENABLED=true` + `FRED_API_KEY=<free key>` for FRED macro lane (Stage 5I)
- Per-lane: `INTEL_V3_FUNDAMENTALS_EVIDENCE_ENABLED`, `INTEL_V3_TECHNICALS_EVIDENCE_ENABLED`, `INTEL_V3_NEWS_SENTIMENT_EVIDENCE_ENABLED`

**Page-load contract preserved:** `GET /intel/v3/snapshot` does NOT call the orchestrator.

## Recent meaningful PRs

Keep this section small. Only entries that affect future work; replace older lines as they age out.

- 2026-05-23 — **Stage 8F: SEC filing-type specificity** — `sec_filing_type_adapter_v1.py` (pure) maps section_reference values from research_artifact_sources to plain-English filing_type_label. `stage8f_filing_type_contract_v1.py` contract marker. `intel_v3_service.py`: `_get_sec_catalyst_artifact_data()` extends prior method with sources SELECT; stage8f added to recertification cascade. `snapshot_builder.py` embeds stage8f marker. Frontend: optional `filing_type_label` on `SecCatalystEvidenceDisplay` and `CatalystEvidenceItem`. 46 backend + 14 new frontend tests. No SQL migrations, no providers, no LLM.

- 2026-05-22 — **Stage 8E: SEC catalyst plain-English explanation layer** — New `sec_catalyst_explanation_adapter_v1.py` (pure) converts existing artifact payload fields into safe display strings. `intel_v3_service.py` adds `_get_sec_catalyst_artifact_payloads()` (fail-soft SELECT on research_artifacts) and merges explanation fields into sec_catalyst_display when sec_catalyst_found=True. Frontend: `SecCatalystEvidenceDisplay` extended with optional explanation fields; `buildCatalystEvidenceDisplay()` uses them when available, falls back to generic Stage 8D copy; editorial_suppressed body clarified when both flags true. 25 backend + 15 new frontend tests. No SQL, no providers, no LLM.

- 2026-05-22 — **Stage 8D: SEC/company catalyst evidence readiness UI surface** — `catalyst_display_adapter_v1.py` converts Stage 5K `AxisReadinessSignal` into three boolean display fields. `intel_v3_service.py` injects these into `research_axis_readiness`. `snapshot_builder` embeds `sec_catalyst_evidence` in `evidence_explanation`. Frontend: `SecCatalystEvidenceDisplay` type, `buildCatalystEvidenceDisplay()`, `CatalystEvidenceModule` in `IntelV3Drawer`. ETFs/crypto: sec_lane_applicable=False → hidden. 18 backend + 21 frontend tests. No SQL, no providers, no LLM.

- 2026-05-18 — **Stage 5I: Add FRED official macro evidence lane v1** — New `fred_provider_v1.py` (typed sync FRED API client, allowlisted 10 macro series, fail-closed on no_api_key/timeout/rate_limit/no_observations) and `fred_macro_adapter_v1.py` (portfolio-scope adapter; artifact_type=`portfolio_exposure`, skill_pack=`fred_macro_evidence_v1`). Macro lane runner `run_fred_macro_evidence()` added to `evidence_lane_runner_v1.py` — one artifact per explicit run, not per ticker. Wired into `intel_v3_evidence_lane_orchestrator_v1.py` fire-and-forget dispatch (fail-soft; empty-ticker early-return removed so macro still fires). Provider registry flips FRED to `default_enabled=True` (requires `FRED_API_KEY`). Two new settings: `intel_v3_macro_evidence_enabled` (default False) + `fred_api_key`. 80 new tests; 109 Stage 5G tests updated to reflect FRED-enabled state (10 assertions retargeted). 724 stage 5A→5I tests pass. No SQL, no UI, no LLM calls, no paid providers, no visible decision changes, no page-load execution. safe_for_decision stays False.

- 2026-05-18 — **Stage 5H.3: Fix SEC CompanyFacts contradiction grouping and ticker eligibility** — SEC-specific contradiction group key in `contradiction_detector_v1` (provider+metric+unit+fy+fp+start+end+frame+filed) prevents distinct XBRL observations from being flagged as contradictions while preserving true conflict detection across filings. Runner adds non-equity guard via `classify_sec_metric_candidate(ticker, category)` before SEC EDGAR lookup; ETF/crypto/fund skipped (BTC, XRP, SPY, etc.) with `sec_companyfacts_skip_non_equity` log. New `sec_companyfacts_usability_summary` runtime diagnostic log. **Patch (same PR):** plumb `holding_context_by_ticker` (`{ticker: {"category": ...}}`) from `IntelV3Service._get_active_holding_context_by_ticker()` → `run_enabled_evidence_lanes_for_portfolio()` → `run_evidence_lanes_for_ticker()` → SEC runner so the guard prefers actual portfolio metadata; the static BTC/XRP/ETF symbol fallback is now only a safety net. Skip log includes `skip_source=metadata|symbol_fallback`. 24 new tests (17 + 7 patch); 431 total stage 5C/5D/5F/5G/5H/5H.1/5H.2/5H.3 tests pass. No SQL, no UI, no LLM calls, no paid providers. Visible Intel decision unchanged.

- 2026-05-18 — **Stage 5H.1: Wire enabled evidence lanes into Intel v3 run path** — `intel_v3_evidence_lane_orchestrator_v1.py` new; `intel_v3_service.enqueue_run_v3()` wired with fire-and-forget `create_task(to_thread(run_enabled_evidence_lanes_for_portfolio, ...))` dispatching after status computation. Runs for ALL tickers even when `analyst_evidence_current`. 28 new tests; 307 stage5e/5f/5g/5h tests still pass. No SQL, no UI, no LLM calls, no paid providers. Visible Intel decision unchanged.

- 2026-05-16 — **Run Intel no-op/current UI fix** — Frontend state-machine bug: `handleRun()` always called `startPolling()`, but when backend returns `analyst_evidence_current` / `queued_ticker_count=0` / `existing_certified_snapshot=true` no new snapshot is created, so `isNewerThanClick` was never satisfied and the spinner ran until 5-min timeout. Fix: detect the no-op case and call `refetchSnapshot()` once instead of starting the polling loop. Added `analyst_evidence_current` to `IntelV3RunResult.status` type. 7 new banner tests; all 431 existing tests still pass.

- 2026-05-16 — **Build 3 PR 2B root-cause fix: valuation context not visible in production (PR opened on branch claude/fix-valuation-context-kakJv)** — Production investigation confirmed root cause: `INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED` env var not set in Railway (defaults `False`), so `_build_valuation_context_map()` returned `None` on every snapshot build and the bridge was never called. Fix: (1) `_build_valuation_context_map()` now logs `valuation_context_pr2b_summary flag_enabled=false/true bridge_not_called=true` on every snapshot build so Railway logs clearly show flag state; (2) `_build()` in `priceband_snapshot_context_v1.py` now emits `valuation_context_pr2b_aggregate_summary` with full counts (total_tickers, company_ticker_count, non_company_suppressed_count, eps_found_count, source_linked_eps_count, fresh_price_count, sector_found_count, priceband_computed_count, renderable_context_count, suppressed_context_count, per-reason suppression counts, fetch_errors) — fires even on the non-company early-return path; (3) 15 new observability tests (all passing). No raw EPS/price/ratios in logs. API contract unchanged. To enable: set `INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED=true` in Railway. After enabling, Railway logs will show `valuation_context_pr2b_aggregate_summary` on every prewarm/snapshot build explaining exactly what is/isn't renderable and why.

- 2026-05-16 — **Build 3 PR 2B: Visible price/valuation context (merged PR #341)** — Grounded plain-English valuation context added to Intel v3 detail drawer (not card/list view). Feature-flagged via `intel_v3_priceband_visible_context_v1_enabled` (default False). 41 backend + 3 frontend contract tests. No SQL, no new providers.

- 2026-05-16 — **Build 2.6: Tighten Intel research freshness SLA** — Recommendation SLA tightened from 24h → 8h; agent insight SLA from 48h → 24h. Changed in three places: `certified_intel_run_contract_v1.py` (RECOMMENDATION_FRESH_HOURS, AGENT_INSIGHT_FRESH_HOURS), `evidence_freshness_contract_v1.py` (SOURCE_SLAS), `watchtower_freshness_ledger_v1.py` (FRESHNESS_SLA_CONFIG). Worker certification now blocks when rec > 8h or insight > 24h. Fast freshness gate queues analyst jobs under new policy. Price refresh / Watchtower / Deploy behavior unchanged. 4 new boundary tests (7h fresh, 9h stale, 23h fresh, 25h stale) + updated comments for old 24h/48h assumptions. No SQL, no UI changes.

- 2026-05-15 — **Build 2.5: Simplified user-facing Intel status** — Replaced large certification/debug banner in `IntelV3Cockpit` with compact `IntelStatusArea`: shows a "Portfolio Intelligence" label, a plain-English status pill (Ready / Updating / Needs Research / Blocked), and one short line. All technical details (agent run IDs, worker_certified, evidence class names) moved into a collapsible "Diagnostics" drawer. Added `buildStatusPillState()` to `intel-v3-banner.ts`; `buildBannerState()` unchanged (tests still green). Button/empty state copy simplified to "Run Intel". Backend certification contract intact.

- 2026-05-15 — **Build 2: Evidence-grade certification + publish contract** — New `watchtower_intel_republisher_v1.py` wires Watchtower evidence freshness → Intel v3 snapshot re-certification. After Watchtower writes fresh `portfolio_snapshots`, `compare_and_republish()` compares timestamps and triggers `IntelV3Service.run_prewarm_snapshot()` (zero LLM calls, all-or-nothing contract re-checked). `get_latest_snapshot()` now embeds `evidence_freshness_state` (`certified_current` | `republish_pending`) in every API response. Worker boundary preserved — no `decide()` import in Watchtower worker. `build_default_intel_republish_callable()` in `watchtower_callables_v1.py` is the boundary-clean wiring. 28 new tests + 91 Build 1D tests green. No SQL.

- 2026-05-15 — **Build 1D patch 3: urgent Watchtower refresh wired to production callables** — Root cause: `run_watchtower_cycle_for_user` in `enqueue_run_v3` passed no `price_refresh_callable`, so the urgent path collected freshness records but never actually refreshed or persisted prices. Fix: extracted `build_default_price_refresh_callable` and `build_default_analyst_enqueue_callable` into new `watchtower_callables_v1.py` (shared, no IO, no side effects at import). `watchtower_worker_entrypoint.py` now delegates to the shared module. `enqueue_run_v3` urgent `create_task` now passes both builders. 7 new tests (88 total, all pass). Key invariant: `price_refresh_callable` is never None in the urgent path.

- 2026-05-15 — **Build 1D patch 2: Deploy strict freshness, price snapshot writer, urgent refresh** — Four remaining pre-merge blockers fixed: (1) **Deploy gate requires FRESH (not AGING)**: `is_deploy_eligible_strict()` added — requires FRESHNESS_FRESH only for deploy-critical types; `build_evidence_record()` now uses `is_deploy_eligible_strict`. A 7-min-old price (Deploy AGING) is now deploy_eligible=False. (2) **Position deploy SLA tightened to 5 min**: `DEPLOY_SLA_CONFIG[POSITION].fresh_seconds=300` — position freshness now tied to price certification cycle; `watchtower_evidence_collector_v1` uses `price_certs.get(t) or snap_at` for position evidence. (3) **Watchtower price refresh now durable**: new `watchtower_price_snapshot_writer_v1.py` — `persist_watchtower_price_snapshot()` reads positions, writes a new `portfolio_snapshots` row with `market_value_certified_at=now` for succeeded tickers, carries forward old values (without cert stamp) for failed tickers. Background worker calls writer after each price refresh. (4) **Run Intel triggers urgent price refresh**: `enqueue_run_v3()` fires `asyncio.create_task(run_watchtower_cycle_for_user(...))` when gate reports price/weight stale; `urgent_refresh_triggered` in response. (5) 22 new tests (81 total, all pass). Key rule: never set `market_value_certified_at` for tickers where price refresh failed.

- 2026-05-15 — **Build 1D patch 1: Watchtower production-usable, gate-first enqueue, strict Deploy SLAs** — Five pre-merge blockers fixed: (1) `DEPLOY_SLA_CONFIG` added to freshness ledger — price/portfolio_weight deploy-fresh now 5 min; `classify_deploy_freshness_status()` uses these stricter thresholds; `build_evidence_record()` computes `deploy_eligible` from Deploy SLA. (2) Gate-first enqueue: `enqueue_run_v3()` runs fast freshness gate BEFORE enqueuing; `_stale_analyst_tickers_from_gate()` extracts only stale/missing analyst tickers. (3) Portfolio weight same Deploy SLA as price. (4) `watchtower_worker_entrypoint.py` created — `--loop`, `INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS` env (default 60s), production callables wired. (5) 20 new tests (59 total). Key logs: `intel_v3_full_refresh_enqueued stale_analyst_count=N gate_succeeded=true/false`.

- 2026-05-15 — **Build 1D: Watchtower Fresh Evidence Foundation** — New modules: `watchtower_freshness_ledger_v1.py`, `watchtower_refresh_planner_v1.py`, `watchtower_evidence_collector_v1.py`, `watchtower_deploy_gate_v1.py`, `intel_v3_fast_freshness_gate_v1.py`, `watchtower_background_refresh_worker_v1.py`. `enqueue_run_v3()` returns `freshness_gate` in response. 39 new tests. No SQL, no schema changes. Certification contract unchanged.

- 2026-05-15 — **Build 1.5 (merged PR #328): Intel v3 sub-10-second user-facing experience + pre-merge patch** — Root cause of multi-minute UX: worker loop ran ONE batch per 60-second sleep. With 34 tickers / 10 per batch = 4 batches × 60s gap = minutes. Fix: (1) `_drain_cycle()` added to entrypoint — runs multiple batches in one cycle when `run_resumable=True` and budget allows (max 8 batches / 300s wall cap); (2) drain cycle stops immediately when `claimed_job_count=0` + `run_resumable=True` (retry backoff — nothing to do, don't spin); (3) `intel_v3.analyst_refresh_worker_drain_cycle_summary` log emits `worker_batches_drained`, `worker_drain_total_duration_ms`, `worker_idle_delay_skipped`, `time_to_worker_certified_snapshot_ms`; (4) backoff stop log: `intel_v3.analyst_refresh_worker_drain_cycle_stopped reason=backoff_or_no_due_jobs`; (5) `get_latest_snapshot()` emits `snapshot_response_ms`; (6) `enqueue_run_v3()` emits `run_click_response_ms`, `certified_snapshot_available_on_click`, `refresh_jobs_pending_count`, `refresh_jobs_remaining_count`; (7) banner `refreshing_analyst_intelligence` copy updated (removed false "60 seconds" claim); (8) `IntelV3Cockpit.tsx` polling guard: `stopPolling()` only fires when `new Date(snap.generated_at).getTime() > refreshStartedAt.current` — amber banner no longer collapses on pre-click certified snapshot. Build 1 trust guarantees intact: prewarm deferred until all jobs drained, certification contract unchanged, no fake freshness. 14 backend tests + 39 frontend banner tests. No SQL, no schema changes, no certification weakening.

- 2026-05-15 — **Build 1 + pre-merge prewarm fix: analyst worker batching end-to-end + early certification guard** — Build 1 root cause: worker claimed 10 jobs but `default_full_portfolio_agent_orchestrator_backend` omitted `analyst_refresh_tickers`, so the LLM stage, `_persist_sync`, and the explicit writeback writer all operated on all 34 holdings. Three surgical scoping fixes. Pre-merge prewarm blocker: the adapter called `_trigger_snapshot_prewarm` after EVERY batch, not just the final one — the certification contract checks ALL active positions, so if the remaining 24 tickers had fresh rows from a prior run, `worker_certified` could publish mid-refresh. Fix: (1) `_trigger_snapshot_prewarm` renamed to `trigger_snapshot_prewarm` (public) with backward-compat alias; (2) per-batch prewarm call removed from `default_full_portfolio_agent_orchestrator_backend`; (3) worker tracks `users_with_successes` and calls `trigger_snapshot_prewarm` only when `run_resumable=False` (all pending/retryable jobs drained); (4) when jobs remain and the pass had successes, emits `intel_v3.analyst_refresh_worker_prewarm_deferred reason=jobs_remain` log. 5 new `TestEarlyPrewarmGuard` tests + updated `test_certification_not_published_until_all_34_pass`. Also fixed a pre-existing Stage 3.2c test compat issue: `_fake_write_sync` mocks now accept `scoped_tickers` kwarg. 42 Build 1 tests + 99 Stage 3.2 / 3.2c tests — all 141 green. No SQL, no schema changes, no frontend changes, no certification weakening.

- 2026-05-14 — **Stage 3.3: All-or-nothing certified intelligence run contract** — Closes the production false-green bug (UI showed green when `claimed=0, llm_calls=0, analyst_refresh_status=not_attempted`). Root cause: `REFRESH_THEN_RUN + trusted` fired when only price was refreshed but analyst evidence was already fresh from a prior worker run, and the click implied agents ran. Fix: (1) `POST /intel/v3/run` now calls `enqueue_run_v3()` — returns `{status:"refresh_requested"}`, zero LLM calls, no snapshot built. (2) New `certified_intel_run_contract_v1.py` — pure async read validator checks 10 conditions per holding. (3) `run_prewarm_snapshot()` runs the contract; sets `snapshot_source="worker_certified"` only if all holdings pass, otherwise `"certification_failed"`. (4) Snapshot carries provenance fields: `snapshot_source`, `certified_holding_count`, `total_holding_count`, `failed_tickers_in_certification`, `certification_summary`. (5) `intel-v3-banner.ts` fully rewritten with 6-state status machine; green requires `worker_certified` + full coverage. (6) `IntelV3Cockpit.tsx` polls every 15s after Run Intel click; stops on `worker_certified` or 5-min timeout. GO decision: Intel v3 now satisfies the all-or-nothing certified intelligence run contract. Green means every active holding passed the evidence contract with matched fresh analyst evidence. No SQL, no schema changes. 17 new backend tests (140 total passing) + 13 new frontend banner tests.

- 2026-05-14 — **Stage 3.2c: Remove double-click Run Intel + fix analyst rationale field loss** — (1) Worker now calls `prewarm_intel_v3_snapshot()` after writing evidence. (2) `_build_analyst_verdict_from_insight()` uses live `AnalystVerdict` objects directly from `orch._verdicts`. Structured logs: `analyst_refresh_snapshot_prewarm_*`. 25 acceptance tests; 169 existing pass.

- 2026-05-14 — **Stage 3.2b + 3.2: Explicit analyst evidence writeback bridge + durable analyst refresh worker** — Stage 3.2b: `AgentOrchestrator._persist_sync` silently fails in Railway worker via thread isolation; `analyst_evidence_writer_v1.write_analyst_evidence()` is the explicit fallback using a fresh `get_supabase_client()`. Idempotent by `UNIQUE(run_id, ticker)`. Stage 3.2: background worker (`AnalystRefreshWorker`) + entrypoint (`--loop`, `INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS` default 60s) claims `analyst_refresh_jobs` rows (SQL migration `018_analyst_refresh_jobs.sql` — apply manually), drives `FullPortfolioAnalystRefreshAdapter` outside the HTTP request. Seam also updated to idempotently enqueue durable jobs so each stale ticker gets a real consumer. Post-run readback fixed: drops fragile ticker-casing filter; scopes by `run_id`/`created_at`; maps case-insensitively. Worker never imports `decide()`. Structured logs: `analyst_evidence_writer_persisted_count`, `intel_v3.analyst_refresh_worker_run_summary`, `intel_v3.analyst_refresh_worker_loop_summary`.

- 2026-05-14 — **Stages 3.0a–3.1: Evidence Refresh Orchestrator + analyst refresh-request seam** — Stage 3.0b: `evidence_refresh_orchestrator_v1.py` classifies per-source freshness, optionally refreshes stale price under deterministic budgets, stamps `run_mode`/`trust_status`. Stage 3.0c: `FullPortfolioAnalystRefreshAdapter` replaces 6-ticker cap; runs AgentOrchestrator unscoped; post-refresh re-read of cards. Stage 3.1: `AnalystRefreshRequestSeam` decouples the synchronous HTTP path from LLM work. `IntelV3Service._build_analyst_refresh_callable()` wires the seam. Frontend amber banner for `refresh_requested` state (`analystRefreshRequestNote()` in `lib/intel-v3-banner.ts`).

- 2026-05-13 — **Stages 2.5A–2.9: Deploy v3 pipeline** — Amount-aware Deploy v3 (new-cash sleeve sizing, certified sizing source adapter, readiness diagnostic, target-allocation + policy bridges, editable execution journal, decision-log history, journal accounting, rounding residual fix, sleeve ranking by Intel conviction). Full details in `docs/product/DECISION_LOG.md`.

- 2026-05-10 — Repo cleanup: removed legacy Streamlit v1 app and added repo hygiene tooling. Full backend suite stabilized at 3,926 passed / 0 failed (before Stage 3 additions).

## Active invariants / safety packs to remember

Named packs in `docs/ai/SAFETY_PACKS_AND_ARCHETYPES.md` (Finance section) own the rules. The packs themselves are the source of truth — do not paste their contents elsewhere:

- Deterministic Decision Authority Pack
- Valuation Safety Pack
- Data Truth / Evidence Suppression Pack
- Deploy/Watchtower Boundary Pack
- Backend-only Scaffold Pack / No Visible Behavior Change Pack / Test Tier Pack (cross-cutting)

## Known risks / unresolved issues

- Deploy item pipeline (dollar math → cash guardrail → finalization → pending-reason) and plan-level rollup are wired backend-only. `tax_guardrail_status` and `wash_sale_guardrail_status` remain `not_evaluated_yet` placeholders — items reach `actionable_pending_tax` / plan reaches `ready_pending_guardrails` honestly, never `actionable`. No fully-actionable final status exists yet (rollup `actionable_count` is reserved at 0).
- Target allocation canonical source (optimizer/service) is not wired — explicit-input only for now; source wiring is deferred to a future stage.
- Watchtower background refresh loop is live in Railway (requires `PROCESS_TYPE=watchtower` + `INTEL_V3_WATCHTOWER_ENABLED=true`). Alert-based push trigger (real-time threshold alerts) is deferred.
- SQL migration 020 has been applied in Supabase (`watchtower_alert_candidates` table + `cooldown_until` column on `action_feedback_events`).
- SQL migration 021 applied — `alert_delivery_outbox` table is live (0 rows expected until eligible candidates flow through).
- SQL migration 022 **applied** — `processing` status, `processing_started_at`/`delivery_attempt_count`/`last_attempt_at` columns, partial index on `alert_delivery_outbox`. Claim-before-send fully operational.
- SQL migration 017 (`research_artifact_store_v1`) — **APPLIED** to Supabase. Creates `research_artifacts`, `research_artifact_sources`, `research_artifact_facts`, `worker_audit_events` tables with RLS, triggers, indexes.
- SQL migration 023 (`023_research_artifact_store_stage5a_extend.sql`) — **APPLIED**. Extends `artifact_type` CHECK with Stage 5A types; adds active-lane uniqueness index and user-scoped replay index.
- Research artifact UX is intentionally deferred until decision/action loop is stable.

## Stage 5H — SEC CompanyFacts Official Fundamentals Adapter v1 (current PR)

**Stage 5H** is the current PR #376 (branch `claude/sec-companyfacts-adapter-v1-ebkW4`).

**What changed in Stage 5H:**
- **`sec_companyfacts_adapter_v1.py`** (new) — Pure, no-IO adapter. Converts `SecEdgarProviderResult` (with parsed XBRL `CompanyFactsParseResult`) → `WorkerOutput`. artifact_type=`fundamental_quality` (existing constraint), skill_pack=`sec_companyfacts_evidence_v1`, model_version=`sec_xbrl_companyfacts_v1`. One `SourceRecord` per unique filing accession (with EDGAR URL, form type, date). One `FactRecord` per `MetricObservation` (preserving period/unit/fiscal_year/fiscal_period/filed/accession_number). Honest thin-evidence on no_cik/timeout/error/no_facts — no fabrication.
- **`evidence_provider_registry_v1.py`** (modified) — Added `LANE_SEC_COMPANY_FACTS = "sec_company_facts"` to constants and `ALL_LANES`. Extended `sec_edgar` entry's `supported_lanes` to include `LANE_SEC_COMPANY_FACTS`. Provider distinction documented: yfinance=FREE/UNOFFICIAL baseline fundamentals; sec_edgar=FREE/OFFICIAL official company-facts lane.
- **`evidence_lane_runner_v1.py`** (modified) — Added `_is_sec_companyfacts_enabled()`, `run_sec_companyfacts_evidence()` (injectable `_provider_fn` for tests; router-consulted; writes via `ResearchArtifactServiceV1`). Extended `run_all_evidence_lanes()` dispatcher with 4th lane + `_sec_companyfacts_provider_fn` parameter.
- **`config.py`** (modified) — Added `intel_v3_sec_companyfacts_evidence_enabled: bool = False` (default OFF).
- **`test_stage5h_sec_companyfacts_adapter.py`** (new) — **75 tests** covering: registry/router structure, adapter SourceRecord/FactRecord with period/unit/accession references, no-data honest paths (no_cik/no_facts/timeout), four enrichment layers in written artifacts, safe_for_decision=False, no intel_v3_snapshots/recommendations writes, kill-switch, dispatcher, paid providers disabled, no decide() import, no ArtifactStoreWriter bypass.

**Providers actually called**: sec_edgar (via `sec_edgar_provider.fetch_for_ticker` when flag on), yfinance (three Stage 5F lanes, unchanged). Paid providers remain disabled.
**SQL required**: NO. `fundamental_quality` already in artifact_type CHECK constraint (migrations 017+023 applied).
**UI changes**: No.
**Deferred XBRL concepts**: All 13 us-gaap allowlisted tags from existing `sec_companyfacts_parser.py` are reused. No new concepts added beyond what Phase 7A parser supports.
**Next stage**: FRED macro lane OR analyst_revisions with richer consensus provider.

## Stage 5G — Provider Registry v1 + Free-First Evidence Source Router (merged)

**Stage 5G** merged (branch `claude/stage-5g-provider-registry-uqK9g`).

**What landed in Stage 5G:**
- **`evidence_provider_registry_v1.py`** — Six providers: `sec_edgar` (FREE/OFFICIAL), `yfinance` (FREE/UNOFFICIAL), `fred` (FREE/OFFICIAL, disabled), `fmp`/`eodhd`/`alpha_vantage` (disabled metadata-only). Registry summary always has `safe_for_decision=False`.
- **`evidence_provider_router_v1.py`** — Deterministic free-first routing policy. `resolve_provider_for_lane(lane)` → `ProviderRouteResult`. Policy: FREE/OFFICIAL → FREE → LOW_COST → PAID → NO_PROVIDER.
- **`evidence_lane_runner_v1.py`** — Router consulted before each Stage 5F lane run. Existing yfinance behavior unchanged.
- **109 tests**. SQL: NO. UI: No.

## Stage 5F — Multi-Lane Evidence Population Pack v1 (merged)

**Stage 5A** merged PR #367. **Stage 5B** merged PR #369. **Stage 5C** merged PR #370. **Stage 5D** merged PR #371. **Stage 5E0** merged. **Stage 5E** merged (branch `claude/finance-tracker-intel-v3-eKyVW`). **Stage 5F** is the current PR (branch `claude/finance-tracker-intel-v3-VPlGv`).

**What changed in Stage 5F:**
- **`evidence_lane_adapter_v1.py`** (new) — Pure, no-IO shared adapter. Three lane adapters: `adapt_fundamentals` → `fundamental_quality` artifact (yfinance fundamentals sync); `adapt_technicals` → `technical_signal` artifact (yfinance history sync); `adapt_news_sentiment` → `sentiment_event` artifact (yfinance news sync). Shared `build_worker_output()` builder. `FEASIBLE_LANES` constant + `DEFERRED_LANES` dict with exact blockers for SEC filing (covered by earnings_reviewer), analyst_revisions (yfinance too thin), company_strategy (no extractor).
- **`evidence_lane_runner_v1.py`** (new) — Dispatcher/registry. Three per-lane runner functions + `run_all_evidence_lanes()` dispatcher. Each lane is kill-switched by `intel_v3_research_workers_enabled` (global) + per-lane flag. All writes go through `ResearchArtifactServiceV1.write_artifact()` (never raw `ArtifactStoreWriter`). Injectable `_fetch_fn` for tests (no real HTTP in tests).
- **`config.py`** — Three new flags: `intel_v3_fundamentals_evidence_enabled`, `intel_v3_technicals_evidence_enabled`, `intel_v3_news_sentiment_evidence_enabled` (all default False).
- **`test_stage5f_multi_lane_evidence.py`** — **63 new tests** proving: all three lanes implemented, all four enrichment layers present in every artifact, safe_for_decision=False, no intel_v3_snapshots/recommendations writes, no decide() import, kill-switch behavior, no fabrication on empty/error data, dispatcher runs all lanes, earnings reviewer path unchanged.

**Lanes inspected**: SEC filing (covered by earnings_reviewer), fundamentals (yfinance), technicals (yfinance), news/sentiment (yfinance), analyst_revisions (thin — deferred), company_strategy (no extractor — deferred).
**Lanes implemented**: fundamentals (`fundamental_quality`), technicals (`technical_signal`), news_sentiment (`sentiment_event`).
**Lanes deferred**: `sec_filing` (earnings_reviewer already covers as `catalyst_window`; a separate `filing_risk` adapter needs XBRL parsing work beyond current scope); `analyst_revisions` (yfinance only provides 2 thin scalars — needs richer consensus provider); `company_strategy` (no guidance/commentary extractor in repo).

**SQL required**: NO. No new tables; existing artifact_type CHECK already supports `fundamental_quality`, `technical_signal`, `sentiment_event` (migration 023 applied).

**Key invariants confirmed**: `safe_for_decision` remains `False`. No Buy/Hold/Trim/Sell authority. No LLM calls. No new external providers. Existing earnings reviewer path intact. No UI changes. ALERT_EMAIL_DRY_RUN untouched.

**Stage 5H next**: SEC company facts lane expansion (wire sec_edgar XBRL company facts into a `sec_company_facts` evidence lane) OR analyst_revisions lane using EODHD or similar consensus provider.

## Stage 5E — Deterministic Research Artifact Truth Adapter v1 (merged)

**Stage 5E** merged (branch `claude/finance-tracker-intel-v3-eKyVW`). `artifact_truth_adapter_v1.py`: six usability labels, injected as Step 7 in `write_artifact()`. All four enrichment layers in every artifact. 37 tests. No SQL.

## Stage 5C — Contradiction Detector v1 (merged PR #370)

**What landed in Stage 5C:**
- `contradiction_detector_v1.py` — Pure deterministic contradiction detector. Grouping key: `(claim_key/metric_name, fact_kind, period, as_of)`. Detects: numeric conflicts (1% tolerance), boolean, text-exact. No-fact → `not_evaluable_reason=no_facts_provided`. Non-comparable → `insufficient_comparable_facts`. `no_guessing=True` always.
- `write_artifact()` Step 5 injects `contradiction_assessment` into payload. Stage 5B credibility (Step 4) intact.
- **41 tests**. No SQL.

## Stage 5B — Source Credibility Registry (merged PR #369)

**What landed:** `source_credibility_registry_v1.py` — 10 source_kinds → 5 authority bands (no numeric scores). Injected into `write_artifact()` Step 4. 83 tests. No SQL.

## Stage 5A — Research Artifact Store (merged PR #367, SQL migrations 017+023 pending Supabase)

**Stage 4 is COMPLETE** (Stage 4H merged as PR #366 on 2026-05-17). **Stage 5A is COMPLETE** (merged PR #367 on 2026-05-18).

**Stage 5A status**: Merged as PR #367 on 2026-05-18.

**What landed in Stage 5A (3 commits):**
- SQL migration `023_research_artifact_store_stage5a_extend.sql` — extends the `artifact_type` CHECK constraint (from migration 017) with 4 new Stage 5A worker types: `technical_signal`, `sentiment_event`, `company_strategy`, `journal_pattern`. Adds user-scoped replay idempotency index (`uq_research_artifacts_replay_user_active`), active-lane uniqueness index (`uq_research_artifacts_active_lane` on `(user_id, artifact_type, skill_pack, scope_kind, COALESCE(ticker, ''))` WHERE `is_active = TRUE`), drops global replay index, and adds a duplicate-lane guard. Migration 017 must be applied first (not yet applied to Supabase).
- `v2/backend/app/services/intelligence/v3/research_artifact_service_v1.py` — narrow typed public API for Stage 5A. Wraps `ArtifactStoreWriter` with two explicit write policies:
  - **Idempotency**: same `replay_idempotency_key` → skip, return existing artifact_id, no duplicate (user-scoped).
  - **Scope-aware clean replacement**: new artifact for same evidence lane `(user_id, artifact_type, skill_pack, scope_kind, COALESCE(ticker, ''))` → deactivate prior active artifacts (`is_active=False, invalidated_at=now, invalidation_reason='superseded_by_new_write'`), insert new. Portfolio-scope (`scope_kind='portfolio'`, `ticker IS NULL`) uses IS NULL filter; ticker-scope uses `.eq("ticker", ticker)`. Always runs clean replacement (no ticker-only guard).
  - `query_active_artifacts()` — safe read helper returning non-payload summary fields only.
  - NEVER imports decide() or writes intel_v3_snapshots. safe_for_decision always False.
- `v2/backend/app/services/intelligence/research_workers/contracts.py` — `WorkerOutput.ticker` changed `str` → `Optional[str]` to represent portfolio-scope artifacts (ticker IS NULL).
- `v2/backend/tests/test_stage5a_research_artifact_store.py` — **60 tests** covering idempotency, scope-aware clean replacement (incl. portfolio-scope, IS NULL filter, cross-scope isolation), provenance, freshness/as_of/expires_at, schema_version, replay/run identity, forbidden key rejection, no Intel v3 decision mutation, all Stage 5A artifact_type values accepted, user-scoped idempotency, fetched_at provenance, migration 023 content.

**SQL required**: YES — two migrations must be applied in order:
1. `v2/database/017_research_artifact_store_v1.sql` — creates research_artifacts, research_artifact_sources, research_artifact_facts, worker_audit_events tables with RLS, triggers, indexes.
2. `v2/database/023_research_artifact_store_stage5a_extend.sql` — extends artifact_type CHECK.

**Existing infrastructure reused (not duplicated):**
- `research_workers/contracts.py` — WorkerInput, WorkerOutput, SourceRecord, FactRecord, forbidden key validation, idempotency key computation.
- `research_workers/artifact_store_writer.py` — DB writer with select-then-insert idempotency.
- `research_workers/artifact_observability.py` — Phase 4 read-only observability.
- `research_workers/artifact_truth_readiness.py` — Phase 5 truth adapter readiness contract.
- `v3/evidence_artifact_contract_v1.py` — EvidenceArtifact mappers.

**Stage 5B next**: Source credibility registry. Stage 5A schema fields (`confidence_or_trust_level`, `deterministic_inputs_allowed`, `safe_for_decision`) leave clean hooks for Stage 5B to fill in without schema churn.

**Stage 4B — Today Command Center** merged as **PR #359** on 2026-05-17. 4 frontend files modified, 2 new files.

What landed:
- `src/app/dashboard/page.tsx`: `/dashboard` reframed as "Today". Above-the-fold: The Brief, Act Today, Risk Pulse, Deploy Ready, Watchtower Summary (all from existing data). "What I Learned Today" Coming-Later chrome (Stage 6E activates). Portfolio snapshot below fold. Hydration-safe `todayLabel` via `useEffect`. `ml-8` replaces fragile calc class.
- `src/lib/today-command-center.ts`: 7 pure deterministic helpers (no LLM, no fabricated claims).
- `src/lib/today-command-center.test.ts`: 49 unit tests, all pass.
- `src/components/navigation/BottomNav.tsx`: `/dashboard` label "Portfolio" → "Today".

**Stage 4A — Design System Foundation + App Shell Reset** merged as **PR #358** on 2026-05-17. Tokens: `tailwind.config.ts`, `globals.css`, `layout.tsx` (fonts), `BottomNav.tsx` (glass chrome).

**Do not skip ahead.** The contract splits the overhaul into:
- **Stage 4** — Quiet Atelier UX foundation + core current-data surfaces (Stage 4A–4H). Frontend only. Done-definition: §35.9.
- **Stage 5** — S-grade Research Artifact + finance-agent intelligence backend (5A–5M). Backend / data only. Done-definition: §35.10.
- **Stage 6** — Advanced evidence, learning, Radar, Journal, command-bar intelligence surfaces (6A–6H). Activates the Coming-Later chrome reserved by Stage 4. Done-definition: §35.11.

Stage 4 must never fabricate Stage 5 / 6 intelligence. Every surface that anticipates a future intelligence module renders the **Coming-Later Pattern** (§28.4): chrome only, with a calm caption that the module is being prepared.

**Execution discipline (contract §35).** Optimize for fast, safe completion: each stage is a meaningful product slice with a visible transformation; no cosmetic micro-builds, no patch loops, no redundant docs, no polish-only PRs (4H is the only exception). Completion target: a transformed, usable, beautiful app first; then deeper S-grade intelligence; then the mentor / learning surfaces.

**Email delivery activation is out of scope for the entire design overhaul.** Email worker remains dry-run on Railway (`ALERT_EMAIL_DELIVERY_ENABLED=true`, `ALERT_EMAIL_DRY_RUN=true`, `ALERT_EMAIL_PROVIDER=resend`). Dry-run log confirmed: `scanned=0 sent=0 failed=0 skipped=0 dry_run=True provider=resend`. **Resend domain verification is still pending — `ALERT_EMAIL_DRY_RUN` must remain `true` until the domain is verified. Do not set `ALERT_EMAIL_DRY_RUN=false` yet. Do not perform real-send validation yet. Real-send activation is reserved as Stage 5M, a separate, non-design stage.**

**Watchtower production requirements (unchanged):** `PROCESS_TYPE=watchtower` + `INTEL_V3_WATCHTOWER_ENABLED=true` on the Watchtower Railway service. `INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED=true` on both main app and Watchtower services.

## Handoff maintenance rule

- This file is current state only. It is not an append-only log.
- Keep under ~250–500 lines. If it grows past that, **compact before adding** — summarize older sections, do not extend them.
- Every meaningful PR may update this file, but by **replacing or summarizing**, never by appending.
- Move durable historical detail to `docs/ai/MISS_LEDGER.md` (workflow/process misses) or `docs/product/DECISION_LOG.md` (product decisions). Do not preserve old noise just because it exists.
- Do not create new archive files for routine PRs. An archive is justified only when current-state value is being replaced and the original detail is still useful elsewhere.
- `CLAUDE.md`, `docs/ai/AI_REPO_OPERATING_SYSTEM.md`, and `docs/ai/PROMPT_LIBRARY.md` enforce this rule.

## Repo hygiene rule (per-PR)

Every meaningful PR must explicitly answer: **"Did this make any source files or tests obsolete?"**

- If yes: delete or rewrite them in the same PR, **or** add a tracked follow-up entry with a one-line reason.
- Run `python3 scripts/repo_hygiene/audit_repo_hygiene.py` before opening the PR. Treat the report as a merge-gate aid, not mandatory CI.
- Rules, allowlist conventions, and test-retirement criteria live in `docs/ai/REPO_HYGIENE.md`.
- `v2/progress_log.md` follows the convention at the top of that file: ~150–250 lines, current state only, no PR-by-PR append.
