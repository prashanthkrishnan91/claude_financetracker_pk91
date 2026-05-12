# Build Queue

The active product queue. Every meaningful implementation PR should map to one item here.

Update via `.claude/skills/build-queue-update/SKILL.md` after meaningful roadmap decisions or merged PRs. Keep updates concise.

## Now

- **Deploy v3 exact-dollar path completion** (Stage 2, still in progress). Sizing adapter is wired (Stage 2.5A). `exact_dollar_ready` can be True in production only when: (1) portfolio snapshot stores `market_value_usd` per position (currently not stored by `create_snapshot`), (2) target allocations are complete, (3) `deploy_minimum_trade_usd` + `deploy_rounding_policy` env vars are set. Next decision: whether to add a snapshot write path that includes per-position market values. Do not exit Stage 2 until a certified action-plan path exists end-to-end.

## Next

- Watchtower trigger foundation (Stage 3 entry). Stays here until Deploy has a useful certified action-plan path.

## Later

- Alerts / action feedback.
- Research artifact UX.
- Premium cockpit design polish.
- Real tax-lot / wash-sale guardrail logic on top of the per-item finalization + plan-rollup contract. Design-dependent: requires explicit tax-lot / trade-history source decisions before any build can start; do not auto-promote into Now.

## Completed

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
