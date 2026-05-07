# Claude Hooks Roadmap — Finance Tracker

Wave 1 does not enable hooks. This roadmap documents future advisory hooks so automation can increase without creating brittle blockers.

## Principles

- Start advisory, not blocking.
- Hook messages should remind Claude which skill/command to run.
- Do not add paid CI, secrets, or expensive runtime calls.
- Keep judgment-heavy decisions in Claude/ChatGPT review, not shell scripts.

## Candidate advisory hooks

| Trigger | Advisory message |
|---|---|
| Intel v3 policy/kernel files changed | Run `/contract-audit`; prove deterministic visible action authority and snapshot safety. |
| Evidence adapter/Data Truth files changed | Run `/claim-safety-gate`; prove weak/missing data suppresses axes instead of fabricating evidence. |
| Research artifact/worker files changed | Run `/claim-safety-gate`; prove artifacts are supporting evidence only and not final action authority. |
| Snapshot/API/client files changed | Run `/contract-audit`; list producer/consumer contract changes. |
| Frontend Intel files changed | Run `/claim-safety-gate`; verify no raw metric/diagnostic/internal label leakage. |
| Migration/Supabase files changed | Fill SQL/manual action fields in PR template. |
| Env/feature flag files changed | Fill env/redeploy/rollback fields in PR template. |
| Docs-only task edits runtime files | Warn and require explicit rationale. |
| Stop event without PR template summary | Remind to run `/pre-pr-self-audit` and `/pr-summary`. |

## Future implementation note

Implement hooks only after this OS is used in real PRs and the highest-value reminders are clear.
