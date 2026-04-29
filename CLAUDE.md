# Claude Instructions — Investing App

## Operating mode

This repo is developed through Claude/Codex in browser or mobile app. Do not assume CLI-only hooks, background agents, swarms, or local terminal orchestration are available unless the user explicitly says they are using CLI.

Primary objective: preserve Claude Pro usage by using repo memory files and compact state packs instead of rediscovering the app.

## Required memory files

Before planning or coding, use these files as the current source of truth:

1. `docs/ai/CLAUDE_WORKFLOW_KIT.md`
2. `docs/ai/HANDOFF.md`
3. `docs/ai/PROMPT_LIBRARY.md`
4. `README.md` only when setup/user-facing behavior is relevant

Do not ignore these files. If the user gives a prompt that conflicts with them, point out the conflict briefly and follow the newest explicit user instruction.

## Project stack

- Frontend: Next.js 14, React 18, TypeScript, Tailwind 3
- Backend: FastAPI
- Database/Auth: Supabase
- Hosting: Vercel frontend, Railway backend
- Primary app path: `v2/`

Important: do not assume Travel Concierge stack details apply here.

## Work rules

- Do only the requested task.
- Prefer smallest safe patch.
- Read only hot files relevant to the task.
- Do not scan the whole repo unless necessary.
- Do not add unrelated refactors.
- Never expose secrets or `.env` contents.
- Always state whether Supabase SQL is required.
- Update `README.md` only when user-visible behavior, setup, migration, or architecture changes.

## Mandatory handoff automation

For every implementation, bug fix, refactor, UI change, migration, or architecture change, update `docs/ai/HANDOFF.md` in the same PR/commit. This is required, not optional.

`docs/ai/HANDOFF.md` must include:

- Last change
- Files touched
- Behavior change
- Known issues
- Next likely task
- Debug notes

Do not end a coding task with "Handoff update needed: Yes" unless you already updated the file. Use "Handoff updated: Yes" or explain why no update was required.

## Response format

Use this order:

1. Root cause or plan
2. Files changed / files to change
3. Tests/checks run or required
4. Risks / rollback notes
5. Supabase SQL required: Yes/No
6. Handoff updated: Yes/No with reason

## Product invariants

- Intel tab must remain concise and signal-rich.
- Deploy tab must preserve clear allocation flow and accurate math.
- Decision logs must capture context without requiring LLM.
- Avoid repetitive UI text and unnecessary duplication.

## Chat strategy

- New feature/fix: new Claude/Codex chat.
- PR review: use Codex or ChatGPT unless implementation reasoning is needed.
- Opus: planning only, produce compact spec, then stop.
- Sonnet: focused implementation.
- Codex: bug fixes, audits, smaller implementation.
