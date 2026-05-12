# Build Queue

The active product queue. Every meaningful implementation PR should map to one item here.

Update via `.claude/skills/build-queue-update/SKILL.md` after meaningful roadmap decisions or merged PRs. Keep updates concise.

## Now

- Plain-English read-only Deploy UI/API surface on top of the existing `DeployPlanRollup` contract — small slice that renders plan readiness (`plan_readiness_status`, counts by final status, counts by pending reason) without re-implementing inference. Real tax/wash-sale guardrail evaluation stays out of scope here and remains honestly `not_evaluated_yet` until separately scoped.

## Next

- Watchtower trigger foundation.

## Later

- Alerts / action feedback.
- Research artifact UX.
- Premium cockpit design polish.
- Real tax-lot / wash-sale guardrail logic on top of the per-item finalization + plan-rollup contract. Design-dependent: requires explicit tax-lot / trade-history source decisions before any build can start; do not auto-promote into Now.

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
