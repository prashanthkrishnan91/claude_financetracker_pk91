# Build Queue

The active product queue. Every meaningful implementation PR should map to one item here.

Update via `.claude/skills/build-queue-update/SKILL.md` after meaningful roadmap decisions or merged PRs. Keep updates concise.

## Now

- **Stage 2.6B — Deploy v3 decision logging** (Stage 2, active). Wire Step 3 so the decision the user logs matches the visible Deploy v3 Step 2 items exactly. Step 3 currently shows a placeholder in the Deploy v3 path; `DecisionLogMemoryPanel` only renders in the legacy fallback path. Stage 2 exit validation follows after Step 2 and Step 3 are coherent — do not attempt exit validation until Step 3 is wired for the Deploy v3 path.

## Next

- **Stage 2 exit validation** — after Stage 2.6B lands, validate the full Step 1/2/3 flow end-to-end in production: Step 2 shows has_moves (or appropriate state), Step 3 logs the correct Deploy v3 decision, readiness gates are green.
- Watchtower trigger foundation (Stage 3 entry). Stays here until Deploy has a certified action-plan path and Step 3 is wired.

## Later

- Alerts / action feedback.
- Research artifact UX.
- Premium cockpit design polish.
- Real tax-lot / wash-sale guardrail logic on top of the per-item finalization + plan-rollup contract. Design-dependent: requires explicit tax-lot / trade-history source decisions before any build can start; do not auto-promote into Now.

## Completed

- **Stage 2.6A** — Deploy v3 powers Step 1/2/3 flow (patched ×2) — Step 1/2/3 is primary UX; Deploy v3 is Step 2 data source; `isActionableMove` requires `recommended_dollar_amount > 0`; Step 2 copy honest (not amount-aware); Step 2/3 coherence enforced — Deploy v3 path shows Step 3 placeholder, does not pass legacy recs into `DecisionLogMemoryPanel`; 46 mapper contract tests (incl. 3 Step 2/3 coherence checks); 266 total; build green. No backend changes, no SQL.
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
