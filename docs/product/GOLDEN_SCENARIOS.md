# Golden Scenarios — Finance

Product invariants future PRs can reuse. Not exhaustive tests — these are the things that must keep working.

Use `.claude/skills/golden-scenarios/SKILL.md` to select 3-7 relevant scenarios per slice.

## Seed scenarios

1. Intel visible action remains deterministic Buy/Hold/Trim/Sell.
2. LLMs / agents / research artifacts never own final visible action authority.
3. Missing / stale / weak data suppresses affected axes instead of fabricating evidence.
4. Deploy can eventually convert certified decisions into exact-dollar action plans.
5. UI remains plain-English with no raw metric keys, diagnostics, shadow labels, or jargon leakage.
6. SQL / env / manual actions are explicit when needed.
7. No auto-trading or broker execution.

## Rules

- Level 2+ implementation prompts should include 3-7 relevant golden scenarios.
- Golden scenarios are not exhaustive tests; they are product invariants.
- If a PR breaks a golden scenario intentionally, the PR must explain why and update `docs/product/ROADMAP.md` and `docs/product/DECISION_LOG.md`.
- New scenarios are added when the roadmap advances a stage; do not bloat this file with edge cases.
