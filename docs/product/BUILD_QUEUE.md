# Build Queue

The active product queue. Every meaningful implementation PR should map to one item here.

Update via `.claude/skills/build-queue-update/SKILL.md` after meaningful roadmap decisions or merged PRs. Keep updates concise.

## Now

- **Stage 2 exit validation** (Stage 2, still in progress). Stages 2.5A–2.6A complete. Step 1/2/3 is the primary UX; Deploy v3 powers Step 2. Validate the end-to-end flow in production: enter investment amount → confirm Step 2 shows has_moves with Deploy v3-backed recommendations. If any gate is still blocked, use "Setup & diagnostics" to fix it. Do not exit Stage 2 until a certified action-plan path exists end-to-end.

## Next

- Watchtower trigger foundation (Stage 3 entry). Stays here until Deploy has a useful certified action-plan path.

## Later

- Alerts / action feedback.
- Research artifact UX.
- Premium cockpit design polish.
- Real tax-lot / wash-sale guardrail logic on top of the per-item finalization + plan-rollup contract. Design-dependent: requires explicit tax-lot / trade-history source decisions before any build can start; do not auto-promote into Now.

## Completed

- **Stage 2.6A** — Deploy v3 powers Step 1/2/3 flow (patched) — Step 1/2/3 is primary UX; Deploy v3 is Step 2 data source (has_moves/no_moves/setup_incomplete/not_available states); diagnostic panels collapsed into "Setup & diagnostics" details section; `deploy-v3-step2-mapper.ts` pure mapper; `isActionableMove` requires `recommended_dollar_amount > 0`; Step 2 copy does not claim sizing for a specific dollar amount (Deploy v3 is not amount-aware; Step 1 amount used only by legacy fallback); 43 mapper contract tests; 263 total; build green. No backend changes, no SQL.
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
