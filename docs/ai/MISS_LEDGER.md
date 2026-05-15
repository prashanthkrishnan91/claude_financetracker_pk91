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

### 2026-05-15 — Usage-ledger instruction repeatedly omitted from generated prompts

Repo: cross-repo
Area: Prompt generation / usage tracking
Severity: Level 2 repeated workflow miss
Miss: Prompts frequently omitted the usage-ledger instruction, resulting in no baseline capture before work and no committed ledger row after. PR bodies then claimed usage tracking without a committed ledger row.
Impact: Incomplete audit trail; readiness checker now enforces the claim-vs-reality mismatch at CI time.
What caught it: Pattern identified across multiple PRs during OS v4 S-grade enforcement review.
Root cause: Usage-ledger instruction was a CLAUDE.md reminder, not a repo-enforced contract. No CI check existed to detect claim-vs-reality mismatch.
What should catch it next time: `scripts/workflow/ai_pr_readiness_check.py` (Check A) hard-fails if PR body claims usage tracked but `docs/ai/USAGE_LEDGER.md` not changed. Usage footer required in PROMPT_ENGINEERING_STANDARD.md and PROMPT_LIBRARY.md templates.
One-off or repeated: Repeated pattern — promoted to gate rule.
Promotion target: ai_pr_readiness_check.py (Check A), PROMPT_ENGINEERING_STANDARD.md, PROMPT_LIBRARY.md.
Action taken: Added readiness checker, CI workflow, usage footer to prompt standards; updated CLAUDE.md hard rules.
Follow-up needed: No.

---

### 2026-05-15 — Same-chat continuation became expensive in production/debug loops

Repo: cross-repo
Area: Chat strategy / cost control
Severity: Level 2 repeated workflow miss
Miss: Same-chat was used for production debugging and multi-PR sequences, causing session context to grow large. Fresh chat was the stated default but not enforced.
Impact: Elevated token burn; session context carried prior-PR content into new slices.
What caught it: Pattern identified in ledger rows with high cumulative costs and multiple same-chat follow-up rows.
Root cause: Fresh-chat rule existed in CLAUDE.md prose but was not checked by any gate.
What should catch it next time: `scripts/workflow/ai_pr_readiness_check.py` (Checks G/H) warns on same-chat + production/debug and on follow-up count > 1 in same-chat.
One-off or repeated: Repeated — promoted to gate rule.
Promotion target: ai_pr_readiness_check.py (Checks G, H), CLAUDE.md PR Readiness Gate.
Action taken: Readiness checker checks G and H added; CLAUDE.md updated.
Follow-up needed: No.

---

### 2026-05-15 — Runtime fixes patched symptoms before proving the failure seam

Repo: cross-repo
Area: Runtime debugging / root-cause quality
Severity: Level 2 repeated workflow miss
Miss: Production-adjacent PRs described symptoms and applied patches without failure-seam evidence (exact log key, test that previously failed, reproduction boundary).
Impact: Follow-up PRs required; ledger showed preventable-follow-up waste.
What caught it: Pattern identified during OS v4 S-grade enforcement review.
Root cause: Runtime validation section in PR template existed but no gate enforced failure-seam evidence when runtime keywords appeared.
What should catch it next time: `scripts/workflow/ai_pr_readiness_check.py` (Check E) hard-fails if PR body references production/runtime/cache without failure-seam evidence.
One-off or repeated: Repeated — promoted to gate rule.
Promotion target: ai_pr_readiness_check.py (Check E).
Action taken: Check E added to readiness checker.
Follow-up needed: No.

---

### 2026-05-15 — Design-overhaul foundation work did not lead to visible adoption

Repo: cross-repo
Area: Design / UI workflow
Severity: Level 2 workflow miss
Miss: Design PRs shipped invisible infrastructure without classifying as foundation-only or planning visible adoption. Some PRs claimed "visual transformation" but changed only CSS token wiring.
Impact: Multiple foundation PRs accumulated without visible user-facing change.
What caught it: Pattern identified during OS v4 S-grade enforcement review.
Root cause: No classification requirement existed in the PR template for design overhaul scope.
What should catch it next time: `scripts/workflow/ai_pr_readiness_check.py` (Check F) requires scope classification and hard-fails if visual transformation claimed without UI validation.
One-off or repeated: Repeated — promoted to gate rule.
Promotion target: ai_pr_readiness_check.py (Check F).
Action taken: Check F added; AI_PR_READINESS_GATE.md documents the design gate.
Follow-up needed: No.

---

### 2026-05-15 — Patch loops continued after repeated misses instead of forcing escalation

Repo: cross-repo
Area: PR workflow / patch exhaustion
Severity: Level 2 repeated workflow miss
Miss: After two related follow-up patches, additional patches continued without fresh-chat escalation or full-plumbing analysis. The reclassification rule existed in CLAUDE.md but was not checked.
Impact: Patch loops accumulated preventable-follow-up waste; root cause remained undiagnosed.
What caught it: Pattern identified during OS v4 S-grade enforcement review.
Root cause: Patch exhaustion rule was instruction-only in CLAUDE.md; no CI check enforced it.
What should catch it next time: `scripts/workflow/ai_pr_readiness_check.py` (Check H) hard-fails on follow-up count >= 3 without escalation note; warns at count 2.
One-off or repeated: Repeated — promoted to gate rule.
Promotion target: ai_pr_readiness_check.py (Check H).
Action taken: Check H added to readiness checker.
Follow-up needed: No.

---

### 2026-05-10 — Workflow/setup asset bloat across root, .claude, docs/ai, and experimental folders

Repo: claude_financetracker_pk91
Area: Workflow architecture hygiene
Severity: Level 2 workflow miss
Miss: Repo accumulated duplicated/orphaned workflow/setup assets — lowercase `claude.md` conflicting with canonical `CLAUDE.md`, `.claude-flow/` capabilities/config from RuFlo V3, ~30 claude-flow skills in `.claude/skills/` (agentdb-*, v3-*, sparc, swarm-*, github-*, hooks-automation, pair-programming, reasoningbank-*, skill-builder, stream-chain, verification-quality, browser), pre-OS-v4 numbered docs in `.claude/`, `graphify-out/` only referenced by the deleted `claude.md`, `tasks/todo.md` only referenced by the deleted `claude.md`, dated transition handoff `HANDOFF_2026-05-10_OS_V4_CONSOLIDATION.md`, duplicate process docs, one-off `INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md` audit, and legacy `docs/ai/skills/` docs-style skill router superseded by `.claude/skills/`. Net: ~75 files removed.
Impact: Bloated workflow surface confused canonical OS v4 entrypoints, made `CLAUDE.md` anchors unreliable, and duplicated rules across multiple owners.
What caught it: PK requested a cross-repo workflow hygiene cleanup.
Root cause: Workflow/setup assets accumulated organically across multiple OS revisions and tool installations (claude-flow / v3 packages) without periodic pruning.
What should catch it next time: After any OS version transition, run a workflow-asset reference scan and delete orphans.
One-off or repeated: First major workflow cleanup; pattern of accumulation is repeated.
Promotion target: Add a periodic "workflow surface scan" step to OS_LEARNING_PROTOCOL or workflow-retrospective skill.
Action taken: Deleted ~75 stale/duplicate/orphaned workflow assets in PR; updated `CLAUDE.md`; recorded this entry.
Follow-up needed: After 1–2 PRs verify nothing depends on the removed claude-flow tooling.

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
