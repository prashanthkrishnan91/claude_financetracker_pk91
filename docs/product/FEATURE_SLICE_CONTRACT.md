# Feature Slice Contract — Finance

Use this template for any Level 2/3 feature slice. The contract must be clear before coding.

## Feature / slice name

## Roadmap alignment

- Roadmap stage:
- Build queue item:
- Why now:
- What this unlocks:
- What this must not expand into:

## User outcome

What the user can do after this ships.

## Product contract

- Entry point:
- User actions:
- Success state:
- Empty / loading / error states:
- Mobile considerations:

## Backend / API contract

- Routes / services touched:
- Payloads changed:
- Compatibility expectations:
- Source-of-truth data (deterministic policy / snapshot / Data Truth):

## Frontend / UI contract

- Screens / components touched:
- State handling:
- Visible copy (plain-English; no raw metric keys / diagnostics / shadow labels / posture labels / jargon):
- No-leakage requirements:

## Data / persistence contract

- Tables / storage touched:
- Migration needed: Yes / No
- Idempotency / rollback considerations:
- SQL / env manual actions:

## Trust / safety contract

- Visible decision authority remains deterministic backend policy.
- LLMs / agents / research artifacts may inform research but cannot own final visible action authority.
- Missing / stale / weak data suppresses affected axes; no fabricated evidence.
- No "safe to act" copy without deterministic support.
- No auto-trading or broker execution.

## Performance contract

- Latency / budget expectations:
- Provider / LLM / cache / persistence considerations:
- Runtime / SQL evidence needed: Yes / No

## Golden scenarios

3-7 scenarios from `docs/product/GOLDEN_SCENARIOS.md` or this slice.

## Out of scope

## Stop / split triggers

- Contract changed mid-implementation.
- Slice grew beyond original scope.
- Required runtime / SQL evidence is unavailable.
- Touches three or more unrelated skill areas.
- Implementation would violate deterministic decision authority.
