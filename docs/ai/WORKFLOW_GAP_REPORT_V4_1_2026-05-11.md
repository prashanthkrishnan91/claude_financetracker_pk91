# Workflow Gap Report vs v4.1 (2026-05-11)

Scope audited: prompt discipline, PR template compliance, usage tracking, docs memory placement, doc bloat prevention, Graphify/context graph availability, hooks/settings safety, workflow files consistency.

## Pass / gap summary

- Prompt discipline: **PASS** (owned by `PROMPT_ENGINEERING_STANDARD.md`, `PROMPT_LIBRARY.md`, `CLAUDE.md`).
- PR template compliance: **PASS with enforcement gap** (template is present and referenced; no CI guard enforces completion).
- Usage tracking: **PASS** (manual + optional Stop-hook snapshot flow documented and wired).
- Docs memory placement: **PASS** (`HANDOFF.md` + durable-memory routing to `MISS_LEDGER.md` / `DECISION_LOG.md` is explicit).
- Doc bloat prevention: **PASS with drift risk** (anti-bloat rules and promotion ladder exist; no automated size/duplication lint).
- Graphify/context graph availability: **GAP (intentional removal, no replacement currently active)**.
- Hooks/settings safety: **PASS with minor inconsistency** (advisory-only hooks and `.env` read-deny are correct; hook file docstring label was stale and is corrected in this PR).
- Workflow files consistency: **PASS with one structural gap** (canonical docs reference `.github/workflows` expectations indirectly, but repository currently has no `.github/workflows/` directory).

## Evidence highlights

- OS v4 canonical workflow and anti-reintroduction constraints are documented in `docs/ai/AI_REPO_OPERATING_SYSTEM.md`.
- Prompt compression/non-boilerplate gate is documented in `docs/ai/PROMPT_ENGINEERING_STANDARD.md` and reinforced in `CLAUDE.md`.
- PR evidence contract exists in `.github/pull_request_template.md`.
- Usage tracking is documented in `docs/ai/AI_USAGE_TRACKING.md` and enabled conditionally in `.claude/settings.json`.
- Memory placement and anti-bloat guidance are documented in `docs/ai/HANDOFF.md`, `docs/ai/MISS_LEDGER.md`, `docs/ai/OS_LEARNING_PROTOCOL.md`, and `docs/ai/REPO_HYGIENE.md`.
- Prior Graphify removal and rationale are captured in `docs/ai/HANDOFF.md` and `docs/ai/MISS_LEDGER.md`.

## Smallest low-risk workflow PR

This PR applies only a no-risk consistency fix:

1. Align `.claude/hooks/ai_os_advisory.py` header text to current OS versioning language (v4).

## Recommended follow-ups (not in this PR)

1. **PR template compliance guard (optional, low-medium risk):** add a lightweight CI check that fails when required template sections are missing/blank.
2. **Doc drift control (optional, low risk):** add a docs hygiene check for oversized `HANDOFF.md` and duplicate workflow anchors.
3. **Workflow consistency (optional, medium risk):** decide whether to add minimal CI workflows or explicitly document "no GitHub Actions" as policy to avoid ambiguity.

## Graphify recommendation

Recommendation: **do not restore Graphify by default** until there is a concrete consumer and maintenance owner.

If restored, keep it strictly optional and artifact-scoped:

- Output path: `docs/ai/context-graph/` (date-stamped snapshots, e.g. `docs/ai/context-graph/2026-05-11.json`).
- Retention: latest + previous only (avoid history bloat).
- Authority: advisory research aid only; never policy/action authority.
