# HANDOFF — Current Repo State

Last updated: 2026-05-12 (post Stage 2.5C + portfolio target total MIN tightened to 98%)

## Purpose

This file is **current operational state**, not a historical log. It is meant to be loaded into context every session, so it must stay compact. Do not append PR-by-PR history. When something changes, replace or summarize the affected section instead of adding new entries.

## Current product stage

- Roadmap stage: **Stage 2.5C + target-total MIN tightened to 98%**. Portfolio-level target allocation total bounds enforced (98%–102%); duplicate DB rows detected and marked CONFLICTING; invalid policy config falls safe (UNSUPPORTED, no crash); 3 new suppression reasons (`TARGET_ALLOCATION_CONFLICTING`, `TARGET_ALLOCATION_TOTAL_OVERALLOCATED`, `TARGET_ALLOCATION_TOTAL_UNDERALLOCATED`) exposed in source metadata. Deploy v3 has no explicit cash/residual target contract; MIN=98% prevents exact-dollar math against incomplete allocation models. 485 deploy tests; 0 failed. Backend-only, no SQL/migration, no UI.
- Active build queue item: **Deploy v3 exact-dollar path completion** — all three exact-dollar readiness gates are now enforced and hardened. `exact_dollar_ready=True` in production requires: (1) fresh snapshot with valid prices (Stage 2.5B), (2) complete valid target allocations for every position ticker with portfolio total in [98%, 102%] (Stage 2.5C), (3) `deploy_minimum_trade_usd` + `deploy_rounding_policy` set (Stage 2.5C). Next: production readiness validation or explicit target allocation setup flow for users.
- Current north-star reminder: Intel → Deploy → Watchtower; deterministic backend policy owns visible Buy/Hold/Trim/Sell authority. See `docs/product/NORTH_STAR.md`.

## Current architecture / runtime state

- OS v4 is the canonical operating system. No v4.2 or v5 labels.
- Visible decision authority is owned by the deterministic Intel v3 backend policy. LLMs / agents / research workers cannot own final visible action authority.
- For long architecture references, read `artifacts/Intel_v3_Architecture_Plan_Draft2_*`, `artifacts/Intel_v3_Architecture_Plan_Draft3_*`, and `artifacts/Intel_v3_Living_Cockpit_Status_Reconciliation_*` rather than copying them here.
- Runtime workflow guardrails: advisory `.claude/hooks/ai_os_advisory.py` reminds about contract / claim-safety / SQL / env paths. No blocking hooks.

## Recent meaningful PRs

Keep this section small. Only entries that affect future work; replace older lines as they age out.

- 2026-05-12 — **Stage 2.5C: Deploy v3 target-allocation + policy readiness hardening v1** — portfolio-level target allocation total bounds (90%–102%) enforced in `target_allocation_ready` and `get_suppression_reasons`; duplicate DB rows → CONFLICTING trust; invalid policy config fails safe (UNSUPPORTED). 3 new suppression reasons in `DeploySizingSuppressionReason`; `TARGET_ALLOCATION_TOTAL_MIN/MAX` constants added. 46 new tests + updated 38 existing tests to use valid target totals; 485 deploy tests total; 0 failed. Backend-only, no SQL, no UI.
- 2026-05-12 — **Stage 2.5B: Deploy v3 snapshot market-value source v1** — `PortfolioService.create_snapshot()` now enriches `positions_data` with `market_price_usd`, `market_value_usd`, `market_value_source`, `market_value_certified_at` when price is valid and fresh. Omits fields when price missing/stale/invalid (fail-safe). Cost basis never stored as market value. Price fetch failure falls back gracefully. Reuses price service cache — no second provider fetch. 25 new backend tests; 64 total for adapter + enrichment (0 failed). No SQL, no new providers, no LLM, no deploy decision changes.
- 2026-05-12 — **Stage 2.5A: Deploy v3 certified sizing source adapter v1** — `deploy_sizing_source_adapter_v1.py` adapter reads `portfolio_snapshots` + `target_allocations` + Settings; certifies sizing inputs via deterministic staleness (24h) and explicit market-value check (cost basis never promoted). Returns `None` when no snapshot exists or DB read fails; returns a non-ready bundle when sources exist but gates fail; only `exact_dollar_ready=True` when all three gates certified. Wired into `GET /api/v1/deploy/v3/plan`; source metadata expanded with `exact_dollar_ready`, `sizing_values_ready`, `target_allocation_ready`, `policy_ready`, `suppression_reasons`. `deploy_minimum_trade_usd` / `deploy_rounding_policy` added to Settings. 51 new backend tests (39 adapter + 12 router); 4469 total backend; 0 failed. Small frontend compatibility patch: `DeployV3Panel` sizing disclaimer now based on `exact_dollar_ready` instead of `sizing_bundle_provided` (stays visible when bundle exists but exact-dollar readiness not yet met); `DeployV3PlanResponse.source` type extended; 24 new frontend contract tests (49 total). No SQL, no providers, no LLM, no broker/execution.
- 2026-05-12 — **Stage 2.4B: Deploy v3 read-only UI surface** — `DeployV3Panel.tsx` component; `api.deployV3.getPlan()` calling `/api/v1/deploy/v3/plan`; `useDeployV3Plan()` hook with query key `["deploy_v3", "plan"]`. Panel renders plan readiness label, counts (pending/blocked/informational/suppressed/not-ready), Intel v3 authority note, and honest sizing-not-connected disclaimer. Handles loading / 404 no-snapshot / flag-off / error / empty states without crashing. Does not call legacy `/allocation/plan` or `/api/deposit-plan` for Deploy v3 data. Legacy deposit workflow untouched. 25 new frontend contract tests; 0 new backend changes. No SQL. No providers. No LLM.
- 2026-05-12 — **Stage 2.4A: Deploy v3 read-only API endpoint** — added `app/routers/deploy_v3.py`; registered in `main.py`. `GET /api/v1/deploy/v3/plan`: authenticated, read-only, mirrors Intel v3 feature flag, returns 404/`no_snapshot` when no snapshot, calls `build_deploy_inputs_from_snapshot` → `build_deploy_plan(sizing_bundle=None)` → returns `plan_status`, `items`, `guardrail_summary`, `rollup`, `source` metadata. No sizing bundle → dollar fields null, `exact_dollar_math_evaluated=false`, BUY/TRIM `final_actionability_status=not_ready` (honest). Legacy `/allocation/plan` unchanged. No UI, no SQL, no providers, no LLM, no broker, no Watchtower. 25 new tests; 4418 total; 0 failed.
- 2026-05-12 — **Stage 2.3E: Deploy plan-level readiness rollup v1** — `deploy_plan_rollup_v1.py` (`build_plan_rollup`, `DeployPlanRollup`); deterministic `plan_readiness_status` ladder; fail-safe unknown bucket. Backend-only; no API/route at that stage.
- 2026-05-11 — **Stages 2.3A–2.3D: Deploy exact-dollar pipeline + per-item finalization** — `deploy_dollar_math_v1.py` (Stage 2.3A; `exact_dollar_ready` gate; BUY/TRIM/SELL deltas; rounding & min-trade suppression; share-quantity only with certified price). `deploy_cash_guardrail_v1.py` (Stage 2.3B; cash certified vs blocked vs not-applicable). `deploy_finalization_v1.py` (Stage 2.3C; per-item `final_actionability_status` ∈ informational_hold | suppressed | blocked_cash | actionable_pending_tax | not_ready). Stage 2.3D added `pending_guardrails_reason` (deterministic across all paths; cleared on non-pending finalization). Item-level intel_action / actionability_status / dollar amount / cash status are never mutated by later stages.
- 2026-05-11 — **Stages 2.0–2.2: Deploy foundation, sizing input contract, policy + target-allocation bridges** — `deploy_contracts.py` (DeployPlan, DeployPlanItem, DeployGuardrailSummary, enums), `deploy_intel_adapter.py` (read-only Intel v3 snapshot reader), `deploy_translation_v1.py` (BUY/TRIM/SELL scaffold; HOLD never actionable; THIN/stale/blocked/weak suppressed; PriceBand never an authority). `deploy_sizing_contracts.py` + `deploy_sizing_builder.py` (Stage 2.1; trust/suppression model; three readiness gates). `deploy_policy_bridge.py` + `deploy_target_allocation_bridge.py` (Stage 2.2; explicit-source-only certification; rejects placeholder labels and fabricated weights).
- 2026-05-10 — Phase 14F closure: added hidden backend-only visible context scaffold (`priceband_visible_context_v1.py`) behind a disabled-by-default config flag. No visible behavior changes, no route, no snapshot writes, no DecisionInputV3 mutation, no Buy/Hold/Trim/Sell changes. **Closed as hidden scaffold only.**
- 2026-05-10 — Final test-suite cleanup: backend full-suite stabilized at **3,926 passed / 0 failed**; 5 stale tests retired with one-line justifications (no production code changed). `audit_repo_hygiene.py` gained an async-test antipattern check. Full backend suite is now a Tier-3 release/infra gate — see `docs/ai/TEST_ROUTING.md`.
- 2026-05-10 — Repo cleanup: removed legacy Streamlit v1 app and added repo hygiene tooling. Deleted `v1/`, root `App.py`, root `requirements.txt`, `.streamlit/`, `.devcontainer/`, the v2 `migration_service.py`, and the `/api/v1/positions/seed-v1` endpoint (zero callers). Compressed `v2/progress_log.md` to a current-state log under the new convention; deleted `v2/progress_log_archive.md`. Added `docs/ai/REPO_HYGIENE.md` and the read-only `scripts/repo_hygiene/audit_repo_hygiene.py` audit. v2 is now the only active product surface.
- 2026-05-10 — workflow architecture hygiene completed (claude-flow stack, helpers, legacy `.claude/commands/`, ~75 stale/duplicate workflow assets, root-surface clutter all removed; canonical anchors are `.claude/skills/`, `docs/ai/TEST_ROUTING.md`, `docs/ai/PROMPT_LIBRARY.md`, `.github/pull_request_template.md`, `docs/ai/SAFETY_PACKS_AND_ARCHETYPES.md`). Active docs/configs no longer reference deleted assets.
- Earlier Intel v3 / Deploy-prep / evidence-check copy work has been folded into product source-of-truth docs and is no longer tracked PR-by-PR here. See `docs/product/DECISION_LOG.md` and `docs/ai/MISS_LEDGER.md` for durable records.

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

**Deploy v3 production readiness validation or target allocation setup flow** (still inside Stage 2). Stages 2.5A–2.5C complete — all three exact-dollar gates are enforced and hardened. For `exact_dollar_ready=True` in production the user still needs: (1) `target_allocations` rows for every current portfolio position with portfolio total in [90%, 102%], and (2) `deploy_minimum_trade_usd` + `deploy_rounding_policy` env vars set. Next decision: whether to build a UI/admin flow for setting target allocations, or run a production readiness check against real data first. Watchtower trigger foundation stays in Next/Later until Deploy has a certified action-plan path.

Real tax-lot / wash-sale guardrail logic is intentionally pending and stays `not_evaluated_yet` at both item and rollup levels until a separately scoped design lands (it requires explicit decisions on tax-lot / trade-history source, cost-basis model, and wash-sale window scope). It is parked under Build Queue → Design Pause Candidates and Later, and must not be auto-promoted into Now by routine queue updates.

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
