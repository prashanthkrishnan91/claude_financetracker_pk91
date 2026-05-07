# AI Repo Operating System — Finance Tracker

This repo uses an AI Repo Operating System so ChatGPT can give Claude short task briefs while Claude performs the repeated engineering workflow automatically.

## Goal
Turn Claude from a prompt executor into a repo-aware engineering partner that plans, audits, tests, delegates independent review, summarizes, and stops at the right boundary.

## Default human/agent loop

1. PK states product goal, issue, screenshot, logs, SQL result, or validation result.
2. ChatGPT chooses severity, model, scope, and gives Claude a short task brief.
3. Claude reads `CLAUDE.md`, this OS, and only the smallest relevant supporting docs.
4. Claude runs the relevant focused skills or commands before coding.
5. Claude builds one focused PR, runs tests, delegates read-only review to applicable reviewer agents, self-audits, updates handoff only when meaningful, and uses the PR template.
6. ChatGPT reviews the actual PR diff and evidence.
7. Codex is used only for surgical blockers, merge-gate exceptions, or targeted audits.
8. PK does UI/runtime/SQL validation only when product-visible behavior or deployment state requires it.

## OS v2 upgrades

OS v2 adds three automation layers on top of v1:

1. **Focused skills** under `.claude/skills/*/SKILL.md` so Claude can invoke smaller task-specific routines instead of skimming one broad checklist.
2. **Advisory hooks** through `.claude/settings.json` + `.claude/hooks/ai_os_advisory.py`. These only print reminders and exit successfully; they do not block tools or change app behavior.
3. **Read-only reviewer agents** under `.claude/agents/*.md` so Claude can delegate independent contract, test, policy-authority, data-truth, SQL/runtime, and plain-English UI review before PR summary.

## Required sequence for non-trivial tasks

Before coding:

1. Classify severity using `docs/ai/ISSUE_SEVERITY_ROUTING.md`.
2. Run or apply `task-planner`.
3. Identify changed contracts and likely downstream consumers.
4. Run or apply `test-selector`.
5. Read `docs/ai/KNOWN_FAILURE_MODES.md` for this repo.

Before PR summary:

1. Run or apply `contract-audit`.
2. Run or apply `runtime-gate` if provider, LLM, db, worker, snapshot, SQL, env, or route behavior changed.
3. Run or apply `claim-safety-gate` if user-visible text/data/actions/evidence changed.
4. Delegate to applicable read-only reviewer agents when the PR touches shared contracts, decision authority, Data Truth, SQL/runtime, visible UI, or snapshot behavior.
5. Run or apply `pre-pr-self-audit`.
6. Fill `.github/pull_request_template.md` honestly through `pr-summary`.

## Reviewer delegation guide

Use reviewer agents for independent evidence, not code edits.

- `contract-auditor`: changed contracts, consumers, missed connected files.
- `test-strategist`: smallest sufficient tests and adversarial invariant coverage.
- `pr-reviewer`: final PR evidence vs checklist.
- `policy-authority-reviewer`: deterministic Intel v3 visible action authority.
- `data-truth-reviewer`: evidence adapters, Data Truth, and suppression behavior.
- `sql-runtime-reviewer`: Supabase SQL, env, deployment, runtime cert, persistence evidence.
- `plain-english-ui-reviewer`: no raw metrics/diagnostics/jargon leakage in UI.

Reviewer agents should return blockers/risks/evidence only. The builder remains responsible for implementation.

## Advisory hooks

Hooks are reminders only in OS v2:

- Intel/policy/snapshot edits remind `/contract-audit`
- evidence/Data Truth/research edits remind `/claim-safety-gate`
- visible Finance UI/client edits remind `/claim-safety-gate`
- SQL/env/settings edits remind manual action fields
- Stop reminds `/pre-pr-self-audit` and `/pr-summary`

Do not treat hook reminders as proof. They are prompts to run the relevant skill or reviewer.

## What belongs in the task prompt

Keep future prompts short. Include only:

- repo
- task/goal
- severity or suspected severity
- success criteria
- hard scope boundaries
- SQL/env/runtime evidence if already known
- screenshots/log excerpts only if needed

Do not paste the full coding principles, repo invariants, test rules, or PR format. They live here.

## What must stay repo-native

- Coding principles: `docs/ai/EXECUTION_PRINCIPLES.md`
- Severity routing: `docs/ai/ISSUE_SEVERITY_ROUTING.md`
- Known failures: `docs/ai/KNOWN_FAILURE_MODES.md`
- Test routing: `docs/ai/TEST_SELECTOR.md`
- Definition of done: `docs/ai/DEFINITION_OF_DONE.md`
- Failure recovery: `docs/ai/FAILURE_RECOVERY.md`
- Runtime evidence: `docs/ai/RUNTIME_EVIDENCE.md`
- Manual actions: `docs/ai/MANUAL_ACTIONS_CHECKLIST.md`

## Claude automation layers

### Layer 1 — Context files
`AGENTS.md`, `CLAUDE.md`, and this OS manual define the repo contract.

### Layer 2 — Focused skills
Use `.claude/skills/*/SKILL.md` for reusable procedures Claude can invoke when context matches.

### Layer 3 — Slash commands
Use `.claude/commands/*.md` as explicit human-triggered shortcuts or aliases to skills.

### Layer 4 — Advisory hooks
Use `.claude/settings.json` + `.claude/hooks/ai_os_advisory.py` as non-blocking reminders.

### Layer 5 — Read-only reviewer agents
Use `.claude/agents/*.md` for independent review without bloating builder context.

## Finance-specific invariants

- Deterministic Intel v3 backend policy owns visible Buy/Hold/Trim/Sell authority.
- LLMs/agents/research workers can produce sourced artifacts only; they cannot own final visible action authority.
- Snapshot endpoints and visible decision contracts must remain source-of-truth consistent.
- Data Truth must suppress affected axes when data is missing/stale/weak/conflicting.
- UI must stay plain-English and avoid raw metric keys, diagnostics, shadow labels, and advanced jargon.
- SQL/env/manual actions must be explicit in PR summaries.

## Stop rules

Stop and ask for a split if:

- The fix touches three or more unrelated skill areas.
- A second related patch would be needed.
- Durable architecture exceeds the stated scope.
- Required runtime or SQL evidence is unavailable.
- The implementation would violate deterministic decision authority.
