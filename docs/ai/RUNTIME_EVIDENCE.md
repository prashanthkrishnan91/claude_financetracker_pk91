# Runtime Evidence — Finance Tracker

Use runtime evidence when local tests cannot prove production behavior.

## Runtime evidence is required when

- Production visible decisions, snapshot endpoints, or runtime certification are the claim.
- Railway logs, UI behavior, SQL results, and local tests disagree.
- Supabase persistence, RLS, migration behavior, or idempotency is involved.
- Provider/worker/LLM/db/cache behavior changed.
- Env flags or deployment behavior affect visibility.

## Runtime evidence is not required when

- The change is docs-only.
- The change is deterministic unit-level logic with sufficient tests and no deploy-specific behavior.
- The change is frontend-only and can be validated by local UI/screenshot evidence.
- The PR only adds internal tests or non-visible logging.

## Preferred evidence order

1. Unit/contract tests for deterministic logic.
2. Snapshot/source-of-truth tests for visible decision contracts.
3. SQL sanity checks for migrations/persistence/RLS.
4. Railway logs for production backend/runtime behavior.
5. Vercel deployment/build logs for frontend deploy issues.
6. Manual UI validation only when visible behavior changed.

## Railway log rule

Use the `railway-logs` personal Claude skill if available. Summarize relevant evidence only. Do not ask PK to upload JSON/logs unless the skill is unavailable or evidence is missing.

## Evidence quality rule

If logs prove only that a route was called but not the behavior under review, say that explicitly and add the missing observability as a follow-up or prerequisite.

## Evidence MCP roadmap

Future optional enhancement:

- GitHub PR/diff evidence can be gathered through GitHub tools.
- Railway runtime evidence can be gathered through the existing `railway-logs` workflow/skill.
- Vercel deployment evidence can be gathered through Vercel tooling when needed.
- Supabase SQL evidence should remain explicit/manual unless safe tooling is available.
- MCP/tool evidence should summarize facts, not replace deterministic tests or human review.

Do not implement new MCP connections in this PR.
