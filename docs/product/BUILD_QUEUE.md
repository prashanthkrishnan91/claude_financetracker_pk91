# Build Queue

The active product queue. Every meaningful implementation PR should map to one item here.

Update via `.claude/skills/build-queue-update/SKILL.md` after meaningful roadmap decisions or merged PRs. Keep updates concise.

## Now

- **Deploy v3 certified sizing bundle source wiring** (Stage 2, still in progress). Read-only UI surface and plan readiness rollup are live, but BUY/TRIM items honestly remain `not_ready` because no sizing bundle is wired. Scope and wire a certified sizing input so dollar amounts can be computed and the Deploy Stage 2 exit gate can be evaluated. Do not exit Stage 2 until a certified action-plan path exists.

## Next

- Watchtower trigger foundation (Stage 3 entry). Stays here until Deploy has a useful certified action-plan path.

## Later

- Alerts / action feedback.
- Research artifact UX.
- Premium cockpit design polish.
- Real tax-lot / wash-sale guardrail logic on top of the per-item finalization + plan-rollup contract. Design-dependent: requires explicit tax-lot / trade-history source decisions before any build can start; do not auto-promote into Now.

## Completed

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
