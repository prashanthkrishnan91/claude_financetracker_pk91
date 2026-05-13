# Build Queue

The active product queue. Every meaningful implementation PR should map to one item here.

Update via `.claude/skills/build-queue-update/SKILL.md` after meaningful roadmap decisions or merged PRs. Keep updates concise.

## Now

- **Stage 2 exit validation** (Stage 2, active). Stages 2.5A–2.6B complete. Validate the full Step 1/2/3 flow end-to-end in production: enter investment amount → Step 2 shows has_moves with Deploy v3-backed recommendations → Step 3 logs the Deploy v3 decision matching visible Step 2 items → readiness gates are green. Use "Setup & diagnostics" to fix any blocked gate. Do not exit Stage 2 or move to Stage 3 until this path is validated. Amount-aware Deploy v3 planning (Stage 2.6C) is a separate explicit decision — do not silently claim it.

## Next

- **Stage 2.6C — Amount-aware Deploy v3 planning** (explicit decision required before build). Make Deploy v3 accept the Step 1 investment amount as a sizing input. Requires product decision on how target-allocation sizing interacts with the entered capital amount.
- Watchtower trigger foundation (Stage 3 entry). Stays here until Deploy / Stage 2 exit validation is confirmed.

## Later

- Alerts / action feedback.
- Research artifact UX.
- Premium cockpit design polish.
- Real tax-lot / wash-sale guardrail logic on top of the per-item finalization + plan-rollup contract. Design-dependent: requires explicit tax-lot / trade-history source decisions before any build can start; do not auto-promote into Now.

## Completed

- **Stage 2.6B** — Deploy v3 decision logging v1 — `DeployV3DecisionLogSection` replaces Step 3 placeholder in Deploy v3 path; `deploy-v3-decision-log.ts` pure helpers; snapshot records `source: "deploy_v3"`, Step 1 amount as context only, visible Step 2 items exactly; no_moves/setup_incomplete → no fake log; 35 contract tests in deploy-v3-decision-log.test.ts; 301 total; build green. No backend changes, no SQL.
- **Stage 2.6A** — Deploy v3 powers Step 1/2/3 flow (patched ×2) — Step 1/2/3 primary UX; Deploy v3 Step 2 data source; `deploy-v3-step2-mapper.ts`; Step 2/3 coherence enforced; 46 mapper contract tests; 266 total; build green. No backend changes, no SQL.
- **Stage 2.5F** — Deploy v3 target allocation setup flow v1 — `DeployV3TargetSetupPanel`; editable target % rows, 98–102% total gate, "use current weights as draft", save calls `PUT /api/v1/portfolio/targets`, invalidates deploy_v3 readiness + plan; `PolicyGuidance` for missing Railway env vars. 29 new contract tests; 200 total; 0 failed. No backend changes, no SQL.
- **Stage 2.5E** — Deploy v3 readiness UI surface v1 — `DeployV3ReadinessPanel` on Deploy page; calls `GET /api/v1/deploy/v3/readiness`; renders gate summary, snapshot status, market value coverage, target allocation gaps, policy status (no values); `useDeployV3Readiness()` hook; `DEPLOY_V3_READINESS_ENDPOINT` constant; `DeployV3ReadinessDiagnostic` TypeScript type; `policyStatusLabel()` helper. 43 new frontend contract tests; 0 backend changes, no SQL.
- **Stage 2.5D** — Deploy v3 production readiness diagnostic v1 — `GET /api/v1/deploy/v3/readiness`; reports all gate statuses, snapshot age, per-ticker market-value coverage, target allocation gaps + total %, policy section (`minimum_trade_configured`, `rounding_policy_configured`, `policy_valid`, `policy_status`; no values exposed), suppression reasons, plain-English `next_required_action`. 49 new tests (42 diagnostic + 7 router readiness); 651 deploy tests total; 0 failed. Backend-only, no SQL, no UI.
- **Stage 2.5C** — Deploy v3 target-allocation + policy readiness hardening v1 — portfolio-level target total bounds (98%–102%) enforced (no cash/residual contract yet; near-full specification required); duplicate DB rows → CONFLICTING trust; invalid policy → UNSUPPORTED fail-safe; 3 new suppression reasons in source metadata. 46 new tests + 38 updated; 485 deploy tests total; 0 failed. Backend-only, no SQL, no UI.
- **Stage 2.5B** — Deploy v3 snapshot market-value source v1 — `PortfolioService.create_snapshot()` enriches `positions_data` with `market_value_usd` per position when price is valid/fresh. Cost basis never promoted. Fail-safe on missing/stale/invalid prices. 33 new backend tests; 72 total for adapter + enrichment (0 failed). No SQL, no providers, no LLM.
- **Stage 2.5A** — Deploy v3 certified sizing source adapter v1 — `deploy_sizing_source_adapter_v1.py` wired into `GET /api/v1/deploy/v3/plan`. Reads `portfolio_snapshots` + `target_allocations` + Settings. Source metadata expanded with readiness gates. Adapter is wired but `exact_dollar_ready` depends on source completeness (see Now). 51 new tests; 4469 total; 0 failed.
- **Stage 2.4B** — Plain-English read-only Deploy v3 UI surface — `DeployV3Panel` on Deploy page; calls `GET /api/v1/deploy/v3/plan`; renders `plan_readiness_status`, counts, Intel v3 authority note, honest sizing-not-connected disclaimer. 25 frontend contract tests; 0 backend changes.
- **Stage 2.4A** — Deploy v3 read-only API endpoint — `GET /api/v1/deploy/v3/plan` live; authenticated, read-only; returns plan rollup from latest Intel v3 snapshot.

## Blocked

- _none recorded_

## Validation Needed

- _none recorded_

## Design Pause Candidates

- Premium cockpit design polish after Deploy / Watchtower loop is stable.
- Real tax-lot / wash-sale guardrail logic — pending an explicit tax-lot / trade-history source design (cost-basis source, lot accounting model, wash-sale window scope).

## Do Not Build Yet

See `docs/product/DO_NOT_BUILD_YET.md`. Highlights:

- auto-trading
- LLM-owned visible financial decisions
- broker execution
- raw metric-heavy UI
- full design sprint before Deploy / Watchtower loop is stable
