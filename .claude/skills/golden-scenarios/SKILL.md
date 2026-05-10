# Skill: golden-scenarios

## Purpose

Select relevant golden scenarios for a task.

## Inputs

- The task brief.
- `docs/product/GOLDEN_SCENARIOS.md`
- `docs/product/ROADMAP.md`
- `docs/product/FEATURE_SLICE_CONTRACT.md` if used.

## Output

- Selected scenarios (3-7):
- Why each matters:
- Tests / evidence expected per scenario:
- Scenarios intentionally out of scope and why:

## Rules

- Use 3-7 scenarios. Avoid copying the entire list.
- Include repo-specific invariants (deterministic visible decisions, no LLM final action authority, honest suppression, plain-English UI, SQL/env discipline).
- If the slice is decision/snapshot/action adjacent, include the deterministic-policy and suppression scenarios.
- If a chosen scenario lacks evidence, flag it and recommend test or runtime evidence.
