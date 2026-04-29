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

Important: do not assume Travel Concierge stack details apply here. This app is Next 14/React 18/Tailwind 3, while Travel Concierge is newer.

## Work rules

- Do only the requested task.
- Prefer smallest safe patch.
- Read only hot files relevant to the task.
- Do not scan the whole repo unless necessary.
- Do not add unrelated refactors.
- Never expose secrets or `.env` contents.
- Always state whether Supabase SQL is required.
- Update `docs/ai/HANDOFF.md` after meaningful code changes.
- Update `README.md` only when user-visible behavior, setup, migration, or architecture changes.

## Response format

Use this order:

1. Root cause or plan
2. Files changed / files to change
3. Tests/checks run or required
4. Risks / rollback notes
5. Supabase SQL required: Yes/No
6. Handoff update needed: Yes/No

## Product invariants

- Intel tab should provide concise ticker reasoning with meaningful data-quality indicators.
- Deploy tab should keep a clear top-to-bottom allocation flow for the user's recurring deposits.
- Allocation math must not be changed casually during UI cleanup.
- Decision logging should capture recommendation context without requiring LLM usage.
- Avoid verbose repetitive card text; prefer compact, inspectable explanations.
- Do not introduce DB migrations unless necessary; if needed, name exact Supabase SQL files.

## Chat strategy

- New feature/fix: new Claude/Codex chat.
- PR review: use Codex or ChatGPT unless implementation reasoning is needed.
- Opus: planning only, produce compact spec, then stop.
- Sonnet: focused implementation.
- Codex: bug fixes, audits, smaller implementation.
