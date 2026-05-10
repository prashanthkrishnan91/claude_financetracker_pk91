# Release Gates

Milestone gates that decide when a stage is ready. Gates are not feature lists; they are go/no-go criteria.

## Intel v3 Certification Gate

- Decisions certified end-to-end.
- No LLM/agent/research final action authority.
- Data Truth suppression honest about missing/stale/weak data.
- Evidence-check copy correct for buy/hold/trim/sell.
- Snapshot persistence consistent.
- No raw metric keys / diagnostics / shadow labels in visible UI.
- SQL/env/runtime certification claims backed by evidence.

## Deploy Readiness Gate

- Decisions certified.
- Action plans exact-dollar and understandable.
- Constraints / guardrails tested.
- Plain-English UI.
- No LLM final action authority.
- Manual actions / SQL / env clear if applicable.
- No "safe to act" language without deterministic support.

## Watchtower Readiness Gate

- Triggers fire only on meaningful changes.
- Suppression rules tested.
- Watchtower data plumbing trustworthy.
- No noisy or false-positive alerts surfaced to user yet.

## Alert Readiness Gate

- Alerts are rare and actionable.
- Alert delivery surface low-noise.
- User can mute / scope without losing intent.
- No marketing or generic-news alerts.

## Finance Design Polish Gate

- Visual system applied consistently across Intel, Deploy, Watchtower, Alerts.
- Plain-English copy pass complete.
- No raw metric keys / diagnostics / shadow labels in visible UI.
- Mobile parity with desktop polish.
- No regressions in core decision/action flows.
