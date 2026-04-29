# Claude Instructions — Investing App

## Operating mode

Browser/mobile Claude + Codex workflow. No CLI-only hooks, swarms, background agents, or local terminal assumptions unless user explicitly says CLI is available.

Primary objective: every Claude/Codex token must move the fix forward. No filler, no broad repo exploration, no speculative rewrites.

## Required memory files

Before planning or coding, use the smallest needed subset of:

1. `docs/ai/HANDOFF.md`
2. `docs/ai/PROMPT_LIBRARY.md`
3. `docs/ai/CLAUDE_WORKFLOW_KIT.md`
4. `README.md`

Do not restate these files. Use them.

## Project stack

- Frontend: Next.js 14, React 18, Tailwind 3
- Backend: FastAPI
- Database/Auth: Supabase
- Hosting: Vercel + Railway
- Primary app path: `v2/`

## Zero-waste work rules

- Every sentence must move diagnosis, implementation, verification, or merge forward.
- Do only the requested task.
- Read only required files.
- Do not scan the repo unless necessary.
- Prefer smallest safe patch.
- Do not refactor unrelated code.
- Do not repeat known architecture.
- Never expose secrets.
- Always state Supabase SQL requirement.

## Mandatory handoff automation

For every implementation, bug fix, refactor, UI change, migration, architecture change, or workflow change, edit `docs/ai/HANDOFF.md` in the same PR.

The task is incomplete if HANDOFF should change and was not updated.

Never ask the user to update HANDOFF manually. Update it.

## Required final response

```md
Root cause/plan:
Files changed:
Tests:
Risks:
Supabase SQL: Yes/No
HANDOFF.md edited: Yes/No + reason
README.md edited: Yes/No + reason
```

## Product invariants

- Intel must stay concise and signal-rich.
- Deploy must preserve allocation math and clarity.
- Decision logs must remain deterministic (no LLM dependency).

## Chat strategy

- Codex: bug fixes, audits
- Sonnet: implementation
- Opus: planning only
