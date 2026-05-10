# HANDOFF — Current Repo State

Last updated: 2026-05-10

## Purpose

This file is **current operational state**, not a historical log. It is meant to be loaded into context every session, so it must stay compact. Do not append PR-by-PR history. When something changes, replace or summarize the affected section instead of adding new entries.

## Current product stage

- Roadmap stage: Stage 1 → Stage 2 transition (Intel v3 correctness/evidence trust → Deploy exact-dollar action plans). See `docs/product/ROADMAP.md`.
- Active build queue item: continue Intel → Deploy transition; define / confirm Deploy action-plan foundation. See `docs/product/BUILD_QUEUE.md`.
- Current north-star reminder: Intel → Deploy → Watchtower; deterministic backend policy owns visible Buy/Hold/Trim/Sell authority. See `docs/product/NORTH_STAR.md`.

## Current architecture / runtime state

- OS v4 is the canonical operating system. No v4.2 or v5 labels.
- Visible decision authority is owned by the deterministic Intel v3 backend policy. LLMs / agents / research workers cannot own final visible action authority.
- For long architecture references, read `artifacts/Intel_v3_Architecture_Plan_Draft2_*`, `artifacts/Intel_v3_Architecture_Plan_Draft3_*`, and `artifacts/Intel_v3_Living_Cockpit_Status_Reconciliation_*` rather than copying them here.
- Runtime workflow guardrails: advisory `.claude/hooks/ai_os_advisory.py` reminds about contract / claim-safety / SQL / env paths. No blocking hooks.

## Recent meaningful PRs

Keep this section small. Only entries that affect future work; replace older lines as they age out.

- 2026-05-10 — Phase 14F closure: added hidden backend-only visible context scaffold (`priceband_visible_context_v1.py`) behind a disabled-by-default config flag. Composes Phase 14D shadow diagnostics with Phase 14E approved visible language into a future-renderable internal context object. No visible behavior changes, no route, no snapshot writes, no DecisionInputV3 mutation, no Buy/Hold/Trim/Sell changes. 57 targeted tests pass; full Phase 14D/14E suites (104 tests) remain green. Phase 14F is **closed as hidden scaffold only**. Next: Stage 2 Deploy action-plan foundation.
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

- Stage 2 (Deploy) entry gate not yet declared formally — the Deploy action-plan foundation is the next piece of plumbing.
- Watchtower trigger model is still scoped but unbuilt; no live alerts.
- Research artifact UX is intentionally deferred until decision/action loop is stable.

## Next recommended step

Clarify or implement the Deploy action-plan foundation slice (one capability slice). Use the OS v4 work-order shape and the Deterministic Decision Authority Pack + Backend-only Scaffold Pack.

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
