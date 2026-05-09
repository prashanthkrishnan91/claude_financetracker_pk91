# AI Repo OS Skill

Use this skill for any non-trivial implementation, bug fix, UI change, provider/runtime change, SQL/migration change, PR review, workflow update, or handoff update.

## Load first

Read only the smallest needed subset of:

- `CLAUDE.md`
- `docs/ai/AI_REPO_OPERATING_SYSTEM.md`
- `docs/ai/KNOWN_FAILURE_MODES.md`
- `docs/ai/TEST_SELECTOR.md`
- `docs/ai/PR_REVIEW_CHECKLIST.md`
- `docs/ai/DEFINITION_OF_DONE.md`
- `docs/ai/MANUAL_ACTIONS_CHECKLIST.md`
- `docs/ai/OS_LEARNING_PROTOCOL.md`
- `docs/ai/MISS_LEDGER.md`
- `docs/ai/WORKFLOW_RETROSPECTIVE.md`

## Task planner

Before coding, state:

- severity level and why
- assumptions
- success criteria
- root cause hypothesis or architecture gap
- affected contracts
- likely downstream consumers
- out-of-scope
- stop/split conditions

Fail planning if the task requires three or more unrelated skill areas in one PR.

## Test selector

Use `docs/ai/TEST_SELECTOR.md`.

- Choose the smallest sufficient suite.
- Add or identify one adversarial test for the riskiest invariant.
- Explain skipped tests.

## Contract audit

List:

- changed outputs/contracts
- consumers
- behavior changes
- files intentionally not changed
- tests or rationale proving safety

Fail the audit if downstream consumers are not checked.

## Runtime gate

Run when provider, LLM, worker, DB, cache, snapshot, SQL, env, or route behavior changes.

Check:

- new live calls/workers/db/cache behavior
- snapshot/source-of-truth impact
- SQL/manual actions
- flag defaults and rollback
- runtime certification/log evidence needed or not

Fail if production behavior is claimed without tests, SQL sanity, runtime cert evidence, logs, or explicit limitation.

## Claim/data-safety gate

Run when user-visible text, cards, actions, decisions, evidence, or LLM-visible prose changes.

Finance checks:

- deterministic Intel v3 policy remains final visible action authority
- LLMs/agents/research artifacts remain supporting evidence only
- finance claims are deterministic, sourced, auditable, or honestly unavailable
- weak/missing/stale data suppresses affected axes instead of fabricating evidence
- raw metrics, metric keys, diagnostics, shadow labels, and jargon cannot leak to UI

## Pre-PR self-audit

Before PR summary:

- Map every success criterion to file/function/test/evidence.
- Identify limitations and out-of-scope items.
- Confirm manual actions checklist.
- Confirm HANDOFF update yes/no and why.
- Fail self-audit if contract, runtime, SQL, or claim/data-safety checks were skipped when applicable.

## PR summary

Use `.github/pull_request_template.md`.

- Do not overclaim.
- Include tests actually run.
- Call out known failures and whether they are pre-existing.
- State SQL/UI/env/provider/LLM/runtime impact.
- State user validation needed yes/no and why.
- Fill the AI workflow retrospective section when applicable: OS skills used, reviewer agents used, miss ledger entry needed yes/no, promotion target if any.
- Classify deployment/build-cost impact when relevant (Vercel deployment expected, preview build needed, deployment-cost risk, docs/workflow-only).

## OS v3 self-learning loop

For Level 1+ PRs, meaningful workflow/product changes, failed validation, Codex rescue, prompt-format miss, deployment/build-cost miss, or repeated follow-up loop:

- run or apply `.claude/skills/workflow-retrospective/SKILL.md`
- if a workflow/product-process miss occurred, run or apply `.claude/skills/miss-ledger-update/SKILL.md`
- use `.claude/agents/workflow-retrospective-reviewer.md` for independent read-only review when a promotion target is proposed
- promote lessons only through `docs/ai/OS_LEARNING_PROTOCOL.md`
- do not bloat the OS from one isolated miss
