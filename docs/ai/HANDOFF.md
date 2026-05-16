# HANDOFF — Current Repo State

Last updated: 2026-05-16 (Build 3 PR 1 — evidence quality visibility)

## Purpose

This file is **current operational state**, not a historical log. It is meant to be loaded into context every session, so it must stay compact. Do not append PR-by-PR history. When something changes, replace or summarize the affected section instead of adding new entries.

## Current product stage

- Roadmap stage: **Stage 3.3 merged** (All-or-nothing certified intelligence run contract). Stage 3.2c complete. Stage 2 exit still pending. Stages 2.5A–2.6D produced amount-aware Deploy v3 with new-cash sleeve sizing. Stage 2.7 turns Step 3 into the user's actual execution journal: editable actual dollar amounts per visible Deploy v3 recommendation (default = recommended), per-row status (BOUGHT / PARTIAL / SKIPPED / WATCHED / TRIMMED / SOLD / HELD), and user-added manual rows (e.g. NVDA BUY $100) clearly labelled as manual via `is_manual: true` on `ActualDecisionItem`. The Step 2 recommendation surface stays read-only. The primary Deploy UX now shows the most recent 10 decision logs below Step 3 using the existing `DecisionHistoryEntry` component (single definition; no parallel history surface). The v3 snapshot mirrors `session_key` and entered/deploy/reserve amounts into `decision_context` so existing fingerprint dedupe + history rendering work uniformly across legacy and v3 logs. Re-logging the same active plan still updates the latest matching log rather than creating duplicate spam. Plain-English clarity note added near Step 3: "These are Intel v3 planning recommendations, not broker-executed trades." No confidence engine, no new provider/market data, no Intel/Deploy sizing changes, no SQL. Frontend-only.
- Active build queue item: **Stage 2 exit validation** — re-validate end-to-end in production with a real dollar amount in Step 1: Step 2 shows 3–5 amount-aware BUY recommendations totaling exactly cash_to_deploy when no guardrail prevents it (e.g. $1,500 = $1,500, not $1,498); Step 3 lets the user edit actual amounts and add manual rows; the decision log history shows BUY spend, manual BUY, and Trim/Sell separately (never negative reserve; "Over planned by $X" / "Unallocated $X" as appropriate); Evaluate button works or gracefully reports insufficient data. Stage 2 exit remains pending until: (1) amount-aware recommendations work, (2) editable actual logging works, (3) decision log history persists after refresh, (4) journal accounting/evaluate behavior is production-validated, (5) recommendation confidence/ranking explanation is reviewed in a later evidence-quality slice.
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

## Recent meaningful PRs

Keep this section small. Only entries that affect future work; replace older lines as they age out.

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
- Watchtower trigger model is still scoped but unbuilt; no live alerts.
- Research artifact UX is intentionally deferred until decision/action loop is stable.

## Next recommended step

**Build 3 PR 1 merged (PR #337).** Evidence quality visibility complete.

**Build 3 PR 1 summary**: Evidence band in visible Intel cards now reflects real evidence quality (AxisBand from `decide()`), not the conviction label. The BUY conviction guardrail promoted from shadow-only to the visible policy via Cap 5 in `_compute_conviction`: HIGH-conviction BUY requires STRONG evidence; OK evidence (1–2 trusted signals) caps conviction at MEDIUM. STUB removed from `_SPECULATIVE_TICKERS` in both `existing_signal_adapter.py` and `portfolio_governor_lite.py`. 31 new tests. Three shadow test assertions updated to reflect that the policy now handles what the shadow guardrail used to do.

**Remaining Build 3 work**: (1) Production validation — confirm `evidence_band` in visible cards now reflects real quality (not conviction); (2) Validate `evidence_freshness_state=certified_current` after Watchtower refresh; (3) Decide next Build 3 slice (analyst evidence depth, primary_driver completeness, rationale quality).

**Next: Build 3 — Intelligence quality GO/NO-GO audit (pre-existing)**: Confirm `evidence_freshness_state=certified_current` in production after Watchtower refresh + prewarm; Confirm `compare_and_republish` logs show `publish_status=rebuilt_and_published` and `analyst_jobs_queued=0` after a price refresh cycle; Check certification contract passes (recs ≤8h, insights ≤24h).

Key logs to confirm Build 1D gate is running in production:
  - `intel_v3_fast_freshness_gate_summary user_id=... intel_status=... deploy_status=... gate_check_ms=N`
  - `watchtower_price_snapshot_writer.snapshot_written user_id=... certified=N carried=N`
  - `intel_v3_urgent_watchtower_refresh_triggered user_id=... deploy_blockers=[price]`
  - Check that `gate_check_ms` stays under 500ms.

**Previous production validation steps (still apply):**

**Production validation of Build 1.5 sub-10s UX + drain cycle.** Re-run the Run Intel v3 button. Key log signals:

- `intel_v3_full_refresh_enqueued ... run_click_response_ms=N` — click must respond in under 1,000ms.
- `intel_v3_snapshot_response_summary ... snapshot_response_ms=N` — page load must return snapshot in under 500ms.
- `intel_v3_full_refresh_enqueued ... certified_snapshot_available_on_click=True` (if a prior certified snapshot exists) — confirms banner shows "Latest Certified Snapshot Available — New Refresh Running."
- `intel_v3.analyst_refresh_worker_drain_cycle_summary worker_batches_drained=4 worker_idle_delay_skipped=True` — confirms 34 tickers drain in 4 batches without 60s artificial gaps.
- `intel_v3.analyst_refresh_worker_drain_cycle_summary time_to_worker_certified_snapshot_ms=N` where `run_resumable_after_cycle=False` — confirms certification completed in one drain cycle.
- Same Build 1 signals still hold: `intel_v3.analyst_refresh_worker_prewarm_deferred reason=jobs_remain` during intermediate batches, `intel_v3_worker_certified_snapshot_published` only after final batch.

**Production validation of Stage 3.3 certified intelligence run contract.** Full expected flow:

1. Click "Run Intel v3" → returns `{status:"refresh_requested"}` — no snapshot built, no LLM calls
2. UI immediately shows "Refreshing Analyst Intelligence" (grey banner); polling starts every 15s
3. Worker picks up jobs within ~60s (Railway log: `intel_v3.analyst_refresh_worker_run_summary` with `claimed>0, succeeded>0`)
4. Worker writes evidence and prewarms: `analyst_evidence_writer_persisted_count=N verdicts_available=N`
5. Contract check: `intel_v3_certified_contract_summary certified=true certified_holding_count=N/N`
6. If contract passes: `intel_v3_worker_certified_snapshot_published` → `GET /intel/v3/snapshot` returns `snapshot_source=worker_certified`
7. UI switches to green "Certified Current" with coverage N/N and latest analyst run timestamp
8. Verify worker logs show `claimed>0` (not 0), `attempted_llm_calls>0` (not 0)
9. Verify snapshot has `agents_ran_via_worker=true`, `this_click_used_llm=false`

**If contract fails:** log shows `intel_v3_worker_certified_snapshot_rejected` with `failed_tickers` and failure reasons; UI shows red "Intel Blocked — Certification Failed" with failed ticker list.

**Prerequisite (if not yet applied):** Apply `v2/database/018_analyst_refresh_jobs.sql`. Ensure Railway worker running with `PROCESS_TYPE=worker`.

**Stage 2 exit validation (re-run).** Open the Deploy page in production, enter $900 in Step 1, confirm Step 2 shows 3–5 amount-aware BUY recommendations, edit one row's actual amount, add a manual NVDA BUY $100, save, refresh, and confirm both rows persist. Confirm decision log history shows BUY spend, manual BUY, and Trim/Sell separately. Repeat with $1,500 and confirm selected BUY recommendations total exactly $1,500. Stage 2 exit remains pending on all five gates listed in Active build queue item above.

Real tax-lot / wash-sale guardrail logic is intentionally pending and stays `not_evaluated_yet` at both item and rollup levels. Parked under Build Queue → Design Pause Candidates. Must not be auto-promoted into Now by routine queue updates.

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
