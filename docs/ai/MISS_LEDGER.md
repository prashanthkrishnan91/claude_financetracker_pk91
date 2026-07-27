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

### 2026-05-15 — Error-swallowing helper masked honest failure state in get_evidence_freshness_state

Repo: claude_financetracker_pk91
Area: backend/intel-v3, watchtower
Severity: Level 1 — caught by test run before merge
Miss: `get_evidence_freshness_state` delegated its DB query to `_fetch_latest_portfolio_snapshot`, which has an internal try/except that swallows all exceptions and returns `None`. The outer try/except in `get_evidence_freshness_state` therefore never fired, and DB errors silently presented as "no portfolio snapshot" → `certified_current` (false-green state). The intent was the opposite: errors should return the honest non-green `republish_pending`.
Impact: In production, a DB read failure during the freshness check would silently tell the API the snapshot is current, hiding the problem from the caller.
What caught it: First test run — `test_returns_republish_pending_on_db_error` asserted `PUBLISH_REPUBLISH_PENDING` but got `PUBLISH_CERTIFIED_CURRENT`.
Root cause: Reusing an error-swallowing helper in a context that requires honest error propagation. The helper was designed for `compare_and_republish` (which has its own outer error boundary) but was copy-reused in `get_evidence_freshness_state` without recognizing the different error-handling contract.
What should catch it next time: When a function's contract distinguishes "no data" from "error", do not delegate to a helper that collapses both to `None`. Inline the DB call so errors propagate to the caller's error boundary.
One-off or repeated: One-off.
Promotion target: None yet.
Action taken: Inlined the `asyncio.to_thread` portfolio snapshot query directly in `get_evidence_freshness_state` so DB exceptions reach the outer `except` and return `PUBLISH_REPUBLISH_PENDING`.
Follow-up needed: No.

---

### 2026-05-15 — OS v4 AI PR Readiness Gate treated usage-unavailable too broadly

Repo: claude_financetracker_pk91 / cross-repo pattern
Area: Workflow enforcement / ledger compliance
Severity: Level 2 workflow enforcement gap
Miss: The initial S-grade readiness gate (ai_pr_readiness_check.py) allowed Level 1+ PRs to skip the `docs/ai/USAGE_LEDGER.md` row entirely by claiming "usage unavailable — <reason>". This made the ledger audit trail incomplete: PRs without ccusage/tooling could omit evidence entirely instead of committing a sanitized row with metadata and unavailable token/delta fields.
Impact: Incomplete ledger trail for Level 1+ PRs; audit loss when tooling was unavailable.
What caught it: V4.1 patch OS review.
Root cause: "Usage unavailable" was conflated with "ledger row unavailable". The intent was to mark token-value fields unavailable, not to waive the ledger row requirement for Level 1+.
What should catch it next time: Strict enforcement in ai_pr_readiness_check.py; Level 1+ always requires ledger row; Level 0 docs-only may skip; unavailable applies only to token/delta fields, not the row itself.
One-off or repeated: One-off enforcement gap; promoted to strict rule.
Promotion target: ai_pr_readiness_check.py (check_ledger), AI_USAGE_TRACKING.md, AI_PR_READINESS_GATE.md.
Action taken: Tightened ai_pr_readiness_check.py to enforce ledger row for Level 1+ regardless of tooling availability; updated docs to clarify "unavailable" scope; added self-tests; updated PR template.
Follow-up needed: No.

---

### 2026-05-15 — AI PR Readiness Gate failed on UI PR missing design scope + reviewer markers

Repo: claude_financetracker_pk91
Area: PR authoring / readiness gate compliance
Severity: Level 1 workflow miss
Miss: PR #328 (Build 1.5) changed `.tsx` UI files but the PR body omitted two markers the readiness gate requires when UI files are present: (1) a design scope classification (`foundation-only` / `visible adoption` / `polish`) and (2) a reviewer note (`no reviewer — <reason>`). Gate failed twice — once on initial PR, once after the patch commit — requiring a third trigger commit to clear.
Impact: Two wasted CI cycles; delayed merge by one session.
What caught it: AI PR Readiness Check CI gate.
Root cause: PR body was authored before running the gate locally with `--base-ref origin/main`. Local run (without `--base-ref`) skips git-diff-dependent checks. The design-scope and reviewer checks only fire when the gate has real file-list context.
What should catch it next time: Always run `python3 scripts/workflow/ai_pr_readiness_check.py --base-ref origin/main` before pushing to a PR branch when UI files (`.tsx`, `.ts`, `.css`) are changed. The `--base-ref` flag is required for full file-list checks.
One-off or repeated: One-off for now; watch for recurrence on UI PRs.
Promotion target: PROMPT_LIBRARY.md UI-PR checklist (add "run readiness gate with --base-ref"), not CLAUDE.md (too specific).
Action taken: Added miss ledger entry. PROMPT_LIBRARY.md update deferred — add if pattern repeats.
Follow-up needed: No.

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

### 2026-05-17 — Policy filtered on serialization label it didn't read (evidence band "OK" vs "PARTIAL")

Repo: claude_financetracker_pk91
Area: backend/alert-policy
Severity: Level 1 — caught by patch review before any production traffic
Miss: `alert_trigger_policy_v1.py` used `_ACTIONABLE_BANDS = {"STRONG", "OK"}` but `snapshot_builder._EVIDENCE_QUALITY_TO_BAND` serializes `AxisBand.OK → "PARTIAL"`. Production cards carry `evidence_band="PARTIAL"` for OK-quality evidence; `"OK"` never appears in real card output. The policy silently suppressed all AxisBand.OK cards.
Impact: No production impact (migration 020 not yet applied, endpoint not yet wired). If shipped as-is, the policy would have missed all real "OK evidence" candidates.
What caught it: Code review of the patch task — reviewer traced the serialization layer to `snapshot_builder._EVIDENCE_QUALITY_TO_BAND`.
Root cause: Policy was written without reading the serialization layer that produces the field values being filtered. The policy test used `evidence_band="OK"` (the axis enum name) rather than `"PARTIAL"` (the serialized display label).
What should catch it next time: When writing a filter/policy on an enum-derived field, read the serialization layer (the module that builds the output dict) before writing the filter. Add a comment in the policy pointing to the mapping source.
One-off or repeated: One-off.
Promotion target: None yet. If it recurs, add a checklist item to the policy archetype in SAFETY_PACKS_AND_ARCHETYPES: "Read the serialization layer before filtering on display-label fields."
Action taken: Fixed `_ACTIONABLE_BANDS` to `{"STRONG","PARTIAL"}`; added source comment; updated suppression message; updated all tests to use `"PARTIAL"`; added 4 new tests explicitly documenting the AxisBand.OK→PARTIAL mapping.
Follow-up needed: No.

---

### 2026-05-17 — Initial PR push used non-template body + missing USAGE_LEDGER row

Repo: claude_financetracker_pk91
Area: PR authoring / workflow compliance
Severity: Level 1 — caught by CI readiness gate before merge
Miss: Initial push for PR #350 used a custom PR body format that omitted required template sections (`## Severity`, `## Validation`, SQL/env/providers/UI, AI usage note, AI PR readiness). USAGE_LEDGER row was also missing. Required two follow-up commits + body update to pass CI.
Impact: Two wasted CI cycles; ~15-min delay.
What caught it: AI PR Readiness Check CI gate.
Root cause: PR body was drafted in the session without the template open. USAGE_LEDGER row was not committed in the same commit as the code.
What should catch it next time: Draft PR body against `.github/pull_request_template.md` before the first push; run `python3 scripts/workflow/ai_pr_readiness_check.py --base-ref origin/main` locally before pushing; commit USAGE_LEDGER row in the same commit as code (or immediately after code, before opening PR).
One-off or repeated: Repeated pattern (see 2026-05-15 entries). Promotion held — gate already catches it; the miss is in execution not tooling.
Promotion target: None (gate already enforces; MISS_LEDGER tracking for pattern count).
Action taken: Added entries; no OS surface changes.
Follow-up needed: No.

---

### 2026-05-17 — USAGE_LEDGER row omitted from initial commit again (PR #352, third occurrence)

Repo: claude_financetracker_pk91
Area: PR authoring / workflow compliance
Severity: Level 1 — caught by CI readiness gate before merge
Miss: PR #352 (Stage 3C) opened without a committed USAGE_LEDGER row. Gate failed twice; required a follow-up commit with the ledger row and a PR body update to pass. This is the third occurrence (#338 patch-1, #350, and now #352) of the same ledger-row miss.
Impact: Two wasted CI cycles; one follow-up patch commit required.
What caught it: AI PR Readiness Check CI gate.
Root cause: Ledger row not committed in the same commit as code. The gate requires `docs/ai/USAGE_LEDGER.md` to be changed for all Level 1+ PRs; "usage unavailable" in the PR body does not waive this.
What should catch it next time: At promotion threshold (3+ occurrences). Recommend adding a checklist line to `pre-pr-self-audit` skill: "Did you commit a USAGE_LEDGER row before the first push?" This catches it before CI runs, not after.
One-off or repeated: Third occurrence — promotion warranted.
Promotion target: `.claude/skills/pre-pr-self-audit/SKILL.md` — add explicit ledger-row checklist item so it fires before the first push to a new PR.
Action taken: Added this MISS_LEDGER entry. SKILL.md update deferred to next session — requires a targeted one-line addition to the pre-PR self-audit checklist.
Follow-up needed: Yes — add "USAGE_LEDGER row committed?" to pre-pr-self-audit checklist.

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

---

### 2026-07-27 — Backend-only downstream grep missed a stray frontend reason-code copy map

Repo: claude_financetracker_pk91
Area: backend/allocation-policy, frontend/paycheck-plan
Severity: Level 1 — caught before merge by reviewer agent, not by the builder's own audit
Miss: While renaming `allocation_policy_v1`'s reason-code strings (`core_etf_preference` → `preferred_core_etf`, etc.), the initial "downstream consumers reviewed" grep was scoped to the backend router (`paycheck_plan_preview.py`) believed to be the sole consumer. It missed `v2/frontend/src/lib/paycheck-plan-helpers.ts`, a reason-code-to-copy map still holding the old strings.
Impact: Low this time (the map is currently unused by any rendered component), but the same miss on a wired-in map would silently show stale/wrong plain-English copy in the UI with no test failure to catch it.
What caught it: A `policy-authority-reviewer` pass, run for an unrelated decision-authority check, happened to scan the whole frontend tree and flagged it.
Root cause: "Downstream consumers reviewed" was interpreted as "the one file I know calls this," not "every string reference to the renamed constant repo-wide."
What should catch it next time: When renaming/removing a backend reason-code or enum string constant, grep the full frontend `src` tree (not just the presumed consumer file) for the old string(s) before claiming downstream consumers were reviewed in a PR body.
One-off or repeated: One-off so far.
Promotion target: None yet — logging only.
Action taken: Synced `paycheck-plan-helpers.ts`'s copy map to the renamed codes in a follow-up commit this PR.
Follow-up needed: No.
