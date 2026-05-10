# Agent Router — Finance

Select relevant reviewer agents. Do not run every agent by default. Reviewer agents return evidence/blockers/risks; they do not write code and they must not bloat builder context.

## Principles

- Use only relevant agents for the change at hand.
- Builder implements; agents review.
- Prefer **fewer high-signal reviewers** over many generic reviewers.
- Reviewer agents must not become a substitute for repo-native safety packs or for the prompt-compression standard.
- Subagents are for independent review and context isolation, not for repeated prompt boilerplate.

## Default routing

- `roadmap-guardian` — product direction, build queue, scope creep, roadmap alignment.
- `contract-auditor` — shared contracts, API, data, frontend ↔ backend boundaries.
- `test-strategist` — non-trivial implementation / test strategy.
- `pr-reviewer` — meaningful PRs before merge.
- `workflow-retrospective-reviewer` — workflow miss or OS promotion candidate.
- `reality-checker` — high-risk, release-readiness, user-facing, or "is this really done?" PRs.
- `evidence-collector` — when proof is scattered across tests/logs/screenshots/runtime/PR evidence.
- `premium-delight-reviewer` — design polish, premium UX, product polish (Stage 6).
- `accessibility-reviewer` — UI / design / mobile / form / navigation changes.
- `performance-benchmarker` — latency, runtime, provider, cache, route, bundle, responsiveness claims.
- `prompt-quality-reviewer` — important generated prompts before PK blind-copies them.
- `eval-scenario-reviewer` — Level 2+ feature slices to verify chosen golden scenarios are appropriate and validated.

## Phase routing

- Pre-coding: `roadmap-guardian`, `prompt-intake-reviewer`, `prompt-quality-reviewer` (for important generated prompts).
- During implementation: route only when changes touch the agent's domain.
- Pre-PR-summary: `pr-reviewer`, `reality-checker` (if user-visible or release-adjacent), `evidence-collector` (if multi-source proof), `eval-scenario-reviewer` (for Level 2+ feature slices).
- Post-merge / failed validation: `workflow-retrospective-reviewer`.

## Finance-specific routing

- `policy-authority-reviewer` — decision / snapshot / action changes (covers Deterministic Decision Authority Pack).
- `data-truth-reviewer` — evidence / Data Truth / source mapping (covers Data Truth / Evidence Suppression Pack).
- `sql-runtime-reviewer` — SQL / env / runtime cert / persistence (covers Runtime/API Contract Pack and SQL/Persistence Manual Action Pack).
- `plain-english-ui-reviewer` — visible UI / copy / card changes (covers Plain-English UI Pack).
- `roadmap-guardian` — Deploy / Watchtower direction.

## High-confidence merge / release gating

- `reality-checker` — for any "is this really done?" claim before merge or before PK is asked to validate.
- `eval-scenario-reviewer` — Level 2+ feature slices to confirm scenario coverage.

## Anti-patterns

- Running every agent on every PR.
- Using reviewer agents to write code.
- Pasting reviewer agent rules into builder prompts (use the safety pack name instead).
- Adding new reviewer agents without recording effectiveness in `AGENT_EFFECTIVENESS_LEDGER.md`.
- Importing external agent libraries wholesale.
- Asking reviewers to report only blockers at the start (use coverage-first audits).
