# HANDOFF — Current Repo State

Last updated: 2026-05-11

## Purpose

This file is **current operational state**, not a historical log. It is meant to be loaded into context every session, so it must stay compact. Do not append PR-by-PR history. When something changes, replace or summarize the affected section instead of adding new entries.

## Current product stage

- Roadmap stage: **Stage 2.2 — Deploy policy + target-allocation readiness bridge complete** (backend-only; next: Stage 2.3 exact-dollar planning math). See `docs/product/ROADMAP.md`.
- Active build queue item: Policy/allocation bridge merged; next item is exact-dollar math using the fully-certified sizing seam. See `docs/product/BUILD_QUEUE.md`.
- Current north-star reminder: Intel → Deploy → Watchtower; deterministic backend policy owns visible Buy/Hold/Trim/Sell authority. See `docs/product/NORTH_STAR.md`.

## Current architecture / runtime state

- OS v4 is the canonical operating system. No v4.2 or v5 labels.
- Visible decision authority is owned by the deterministic Intel v3 backend policy. LLMs / agents / research workers cannot own final visible action authority.
- For long architecture references, read `artifacts/Intel_v3_Architecture_Plan_Draft2_*`, `artifacts/Intel_v3_Architecture_Plan_Draft3_*`, and `artifacts/Intel_v3_Living_Cockpit_Status_Reconciliation_*` rather than copying them here.
- Runtime workflow guardrails: advisory `.claude/hooks/ai_os_advisory.py` reminds about contract / claim-safety / SQL / env paths. No blocking hooks.

## Recent meaningful PRs

Keep this section small. Only entries that affect future work; replace older lines as they age out.

- 2026-05-11 — **Stage 2.2: Deploy policy + target-allocation readiness bridge** — added `deploy_target_allocation_bridge.py` (certify_target_allocation, build_certified_target_allocations: validates explicit-source only, rejects placeholder labels, rejects fabricated weights) and `deploy_policy_bridge.py` (certify_sizing_policy, build_policy_from_config: validates WHOLE_DOLLAR/NEAREST_DOLLAR/NO_ROUNDING, non-negative min_trade). Bridges into DeploySizingInputBundle without changing Intel authority. Production/default builder path stays UNSUPPORTED/NOT_EVALUATED (exact_dollar_ready=False). Synthetic certified sizing+target+policy → exact_dollar_ready=True (readiness gate only, no dollar math). 48 Stage 2.2 + 118 Stage 2.1 + 74 Stage 2.0 = 240 passed / 0 failed. No SQL, no UI, no routes, no providers, no visible behavior change.
- 2026-05-11 — **Stage 2.1: Deploy sizing input contract** — added `deploy_sizing_contracts.py` and `deploy_sizing_builder.py`. Trust/suppression model; three readiness gates; target allocations NOT_EVALUATED placeholder; policy UNSUPPORTED placeholder. 143 focused tests passed. No SQL, no UI, no routes, no visible behavior change.
- 2026-05-11 — **Stage 2.0: Deploy Foundation v1** — new backend-only domain seam (`app/services/deploy/`). Created `deploy_contracts.py` (DeployPlan, DeployPlanItem, DeployGuardrailSummary, enums), `deploy_intel_adapter.py` (reads Intel v3 snapshot read-only), `deploy_translation_v1.py` (translates BUY/TRIM/SELL to scaffold candidates; HOLD → never actionable; THIN/stale/blocked suppresses). All dollar fields null in v1. PriceBand not a Deploy authority. 74 focused tests pass. No SQL, no UI, no routes, no providers, no visible behavior change.
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

- Deploy exact-dollar math is not yet implemented — all dollar fields remain null in the Deploy plan items. The sizing input seam is now fully bridged (Stage 2.2); exact-dollar math can now be safely implemented in Stage 2.3 using the certified DeploySizingInputBundle.
- Target allocation canonical source (optimizer/service) is not wired — explicit-input only for now; source wiring is deferred to a future stage.
- Watchtower trigger model is still scoped but unbuilt; no live alerts.
- Research artifact UX is intentionally deferred until decision/action loop is stable.

## Next recommended step

Stage 2.3 — Deploy exact-dollar math: implement recommended_dollar_amount and estimated_share_quantity calculation using the certified DeploySizingInputBundle (exact_dollar_ready=True path). Use the Deploy/Watchtower Boundary Pack + Deterministic Decision Authority Pack. Prerequisite: DeploySizingInputBundle.exact_dollar_ready is True.

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
