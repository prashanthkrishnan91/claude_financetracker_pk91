# Build Queue

The active product queue. Every meaningful implementation PR should map to one item here.

Update via `.claude/skills/build-queue-update/SKILL.md` after meaningful roadmap decisions or merged PRs. Keep updates concise.

## Now

- Deploy real tax-lot / wash-sale guardrail logic on top of the per-item finalization + plan-rollup contract (today's `tax_guardrail_status` and `wash_sale_guardrail_status` are honest `not_evaluated_yet` placeholders).

## Next

- Plain-English Deploy UI (action-plan surface) on the existing `DeployPlanRollup` contract.
- Watchtower trigger foundation.

## Later

- Alerts / action feedback.
- Research artifact UX.
- Premium cockpit design polish.

## Blocked

- _none recorded_

## Validation Needed

- _none recorded_

## Design Pause Candidates

- Premium cockpit design polish after Deploy / Watchtower loop is stable.

## Do Not Build Yet

See `docs/product/DO_NOT_BUILD_YET.md`. Highlights:

- auto-trading
- LLM-owned visible financial decisions
- broker execution
- raw metric-heavy UI
- full design sprint before Deploy / Watchtower loop is stable
