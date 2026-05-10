# Miss Ledger

Use this file only for workflow/product-process misses, not every app bug.

## Entry template

### YYYY-MM-DD — <short title>

Repo:
Area:
Severity:
Miss:
Impact:
What caught it:
Root cause:
What should catch it next time:
One-off or repeated:
Promotion target:
Action taken:
Follow-up needed:

---

### 2026-05-10 — Workflow/setup asset bloat across root, .claude, docs/ai, and experimental folders

Repo: claude_financetracker_pk91
Area: Workflow architecture hygiene
Severity: Level 2 workflow miss
Miss: Repo accumulated duplicated/orphaned workflow/setup assets — lowercase `claude.md` conflicting with canonical `CLAUDE.md`, `.claude-flow/` capabilities/config from RuFlo V3, ~30 claude-flow skills in `.claude/skills/` (agentdb-*, v3-*, sparc, swarm-*, github-*, hooks-automation, pair-programming, reasoningbank-*, skill-builder, stream-chain, verification-quality, browser), pre-OS-v4 numbered docs in `.claude/`, `graphify-out/` only referenced by the deleted `claude.md`, `tasks/todo.md` only referenced by the deleted `claude.md`, dated transition handoff `HANDOFF_2026-05-10_OS_V4_CONSOLIDATION.md`, duplicate process docs (`PROMPT_BRIEF_TEMPLATE`, `TEST_SELECTOR`, `PR_REVIEW_CHECKLIST`, `CLAUDE_WORKFLOW_KIT`, `CLAUDE_PERSONAL_SKILLS`, `CLAUDE_HOOKS_ROADMAP`, `SUBAGENTS_ROADMAP`, `CONTEXT_MANAGEMENT`, `GITHUB_LABELS`, `HOOK_SAFETY`, `MANUAL_ACTIONS_CHECKLIST`, `UI_BASELINE`, `USAGE_LEDGER`, `PERMISSIONS_AND_MEMORY_BOUNDARIES`, `AI_OS_MANIFEST`, `NEW_REPO_BOOTSTRAP`), one-off `INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md` audit, and legacy `docs/ai/skills/` docs-style skill router superseded by `.claude/skills/`. Net: ~75 files removed.
Impact: Bloated workflow surface confused canonical OS v4 entrypoints, made `CLAUDE.md` anchors unreliable (broken `docs/ai/skills/README.md` ref), and duplicated rules across multiple owners.
What caught it: PK requested a cross-repo workflow hygiene cleanup.
Root cause: Workflow/setup assets accumulated organically across multiple OS revisions and tool installations (claude-flow / v3 packages) without periodic pruning.
What should catch it next time: After any OS version transition, run a workflow-asset reference scan and delete orphans. Do not install third-party tool skills/agents into `.claude/` unless they're routed by canonical OS docs. Bootstrap docs (`AI_OS_MANIFEST`, `NEW_REPO_BOOTSTRAP`) should not encourage copying every doc into new repos.
One-off or repeated: First major workflow cleanup; pattern of accumulation is repeated.
Promotion target: Add a periodic "workflow surface scan" step to OS_LEARNING_PROTOCOL or workflow-retrospective skill.
Action taken: Deleted ~75 stale/duplicate/orphaned workflow assets in PR; updated `CLAUDE.md` to drop the broken `docs/ai/skills/README.md` anchor and point at `.claude/skills/` directly; recorded this entry. Note: `docs/ai/HANDOFF.md` exceeded ~378k chars and could not be updated in-place via the GitHub Contents API; cleanup is documented here and in the PR summary instead.
Follow-up needed: After 1–2 PRs verify nothing depends on the removed claude-flow tooling. Some empty/orphaned files may remain inside deleted skill folders (only their `SKILL.md` was removed) — clean those up opportunistically.

---

## Seed entries

### 2026-05-07 — Old-format prompt after OS v2

Repo: Travel Concierge / cross-repo workflow
Area: Prompt generation
Severity: Level 2 workflow miss
Miss: ChatGPT generated a Travel project prompt that did not use OS v2 work-order format even after PK explicitly requested a v2-based prompt.
Impact: PK had to catch the workflow regression manually; future Claude prompts could bypass the new OS.
What caught it: PK review.
Root cause: Prompt-generation standard was not enforced by the repo OS or prompt template strongly enough.
What should catch it next time: PROMPT_LIBRARY / PROMPT_ENGINEERING_STANDARD, `.github/pull_request_template.md`, workflow-retrospective skill.
One-off or repeated: First recorded miss, but high-signal.
Promotion target: PROMPT_LIBRARY / PROMPT_ENGINEERING_STANDARD and `.github/pull_request_template.md`.
Action taken: OS v3 requires all future Travel/Finance/future-repo coding prompts to use OS v2/v3 work-order format unless explicitly generating architecture/spec only.
Follow-up needed: Verify future prompts include required OS skills, reviewer agents, and stop condition.

### 2026-05-07 — Deployment storm from file-by-file connector commits

Repo: Travel Concierge / Finance Tracker / cross-repo workflow
Area: Deployment/build-cost control
Severity: Level 2 workflow miss
Miss: Bulk workflow docs were updated through ChatGPT GitHub connector as many file-by-file commits, triggering many Vercel deployments and exhausting usage.
Impact: Avoidable deployment usage spike and workflow friction.
What caught it: PK observed Vercel usage impact.
Root cause: Bulk repo update was performed through connector write actions instead of a batched Claude/Sonnet branch/PR.
What should catch it next time: SAFETY_PACKS_AND_ARCHETYPES (Deploy/Watchtower Boundary Pack), `.github/pull_request_template.md` (manual actions / SQL / env fields), prompt-generation behavior.
One-off or repeated: First recorded miss, high-cost.
Promotion target: `.github/pull_request_template.md` and SAFETY_PACKS_AND_ARCHETYPES.
Action taken: Bulk repo/workflow edits should be done by Claude/Sonnet as one PR, not through ChatGPT connector file-by-file writes.
Follow-up needed: Future ChatGPT should generate Sonnet work-order prompts for bulk repo edits.

### 2026-05-07 — Follow-up loop from incomplete downstream contract audits

Repo: Travel Concierge / Finance Tracker
Area: PR completeness
Severity: Level 2 repeated workflow pattern
Miss: Multiple historical PRs required follow-up fixes because local implementation was not tied tightly enough to downstream contracts, tests, runtime evidence, or visible UI/API consumers.
Impact: More prompts, more PR churn, more PK validation, slower build velocity.
What caught it: ChatGPT PR review, PK UI validation, runtime logs, Codex/Claude follow-ups.
Root cause: The builder often proved local behavior but not the full product invariant or downstream contract.
What should catch it next time: contract-auditor, test-strategist, pr-reviewer, pre-pr-self-audit, TEST_ROUTING.
One-off or repeated: Repeated pattern.
Promotion target: reviewer agents and TEST_ROUTING.
Action taken: OS v2 added focused skills and read-only reviewer agents; OS v3 adds retrospective/learning loop.
Follow-up needed: After next few PRs, check whether reviewer agents reduce follow-up prompts.
