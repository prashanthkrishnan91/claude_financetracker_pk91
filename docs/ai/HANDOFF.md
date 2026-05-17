# HANDOFF — Current Repo State

Last updated: 2026-05-17 (Stage 4B — Today Command Center; **merged PR #359**; next: Stage 4C)

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

## Recent meaningful PRs

Keep this section small. Only entries that affect future work; replace older lines as they age out.

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
- Research artifact UX is intentionally deferred until decision/action loop is stable.

## Current design stage — Stage 4C (next)

**Stage 4B — Today Command Center** merged as **PR #359** on 2026-05-17. 4 frontend files modified, 2 new files.

What landed:
- `src/app/dashboard/page.tsx`: `/dashboard` reframed as "Today". Above-the-fold: The Brief, Act Today, Risk Pulse, Deploy Ready, Watchtower Summary (all from existing data). "What I Learned Today" Coming-Later chrome (Stage 6E activates). Portfolio snapshot below fold. Hydration-safe `todayLabel` via `useEffect`. `ml-8` replaces fragile calc class.
- `src/lib/today-command-center.ts`: 7 pure deterministic helpers (no LLM, no fabricated claims). Zero-candidate Watchtower copy is calmer: "currently waiting for review" (no "within normal thresholds" overclaim).
- `src/lib/today-command-center.test.ts`: 49 unit tests, all pass.
- `src/components/navigation/BottomNav.tsx`: `/dashboard` label "Portfolio" → "Today".

**Next: Stage 4C — Intel Investment Committee Redesign.** Action Card visual system, Confidence Ring, Risk Glyph, Freshness Dot, Detail Drawer with existing Intel v3 data. Coming-Later chrome for future capsules. No backend, no SQL.

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
