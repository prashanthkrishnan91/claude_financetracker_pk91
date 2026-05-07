# AGENTS.md — Finance Tracker

This is the portable entrypoint for Claude, Codex, ChatGPT-driven agents, and future AI coding tools. Keep it short; detailed procedures live in `docs/ai/` and `.claude/skills/`.

## Repo mission
Build a best-in-class personal investment intelligence and execution cockpit with deterministic, auditable Buy/Hold/Trim/Sell decisions, sourced evidence, plain-English UI, and future research workers that support rather than replace policy authority.

## Required operating system
For non-trivial work, follow `docs/ai/AI_REPO_OPERATING_SYSTEM.md`.

Permanent rules:

- Read `CLAUDE.md` first when using Claude Code.
- Use repo-local skills and commands instead of pasting long repeated instructions.
- Classify severity before implementation.
- State assumptions, success criteria, affected contracts, and stop/split conditions before coding.
- Audit downstream consumers before opening a PR.
- Use `.github/pull_request_template.md` for PR evidence.
- Update `docs/ai/HANDOFF.md` only for meaningful product, architecture, migration, workflow, or major bug-fix changes.

## Non-negotiable product invariants

- Deterministic backend Intel v3 policy owns visible Buy/Hold/Trim/Sell action authority.
- LLMs, agents, and research workers may produce sourced artifacts, but must never own final visible action authority.
- Do not expose raw backend metrics, raw metric keys, internal diagnostics, shadow labels, or advanced finance jargon in UI.
- Frontend must stay plain-English for amateur investors.
- Any finance data claim must be deterministic, sourced, auditable, or honestly unavailable.
- Missing/stale/weak data must suppress affected axes, not fabricate evidence.

## Default agent roles

- ChatGPT: product architect, prompt engineer, PR reviewer, workflow owner.
- Claude Sonnet: primary focused feature/fix builder.
- Claude Opus: architecture/spec/planning only.
- Codex: surgical blockers, focused audits, small tests/refactors, merge-gate exceptions.

## Stop condition
If the durable fix exceeds scope, stop and propose a split. Do not patch around a deeper architecture gap.
