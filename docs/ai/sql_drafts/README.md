# `docs/ai/sql_drafts/` — Draft-Only SQL Proposals

This directory holds **draft SQL proposals only**. Files here are NOT applied to any
environment, including Supabase production, staging, or local. They live outside
`v2/database/` precisely so the standard migration apply path cannot pick them up
by accident.

## Promotion path

A draft is promoted by an explicit follow-on PR that:

1. Re-reviews the draft against the corresponding architecture spec doc in
   `docs/ai/`.
2. Resolves any open questions called out in that spec doc.
3. Renames the file to the next migration number under `v2/database/` (e.g.
   `017_<name>.sql`).
4. Applies it to Supabase via the SQL Editor in a maintenance window.
5. Updates `docs/ai/HANDOFF.md` and `v2/progress_log.md` with a "Supabase SQL: Yes"
   entry.

Until that follow-on PR exists, drafts in this directory are reference material
and a review surface only.

## Conventions

- Every draft must begin with a banner that says
  `DRAFT ONLY — DO NOT APPLY TO PRODUCTION UNTIL APPROVED`.
- Every draft should mirror the v2 migration style: additive, `IF NOT EXISTS`
  guards, `DO $$` policy guards, RLS enabled where the data is user-scoped,
  comments on critical columns.
- Every draft should include a clearly-marked DRAFT ROLLBACK section — commented
  out by default — that drops what the draft creates.
- Drafts may not be referenced by runtime code in `v2/`. If runtime code needs
  to read or write the new tables, that work belongs in a separate PR after
  promotion.

## Current drafts

- `research_artifact_store_v1.sql` — Phase 2 Research Artifact Store v1. See
  `docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md` for the design spec and the
  Phase 1 architecture spec (`docs/ai/INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md`)
  for the binding contracts this draft satisfies.
