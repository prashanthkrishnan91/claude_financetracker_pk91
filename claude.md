# Project Rules

## Stack
Vercel (serverless) · Supabase (DB) · Python/JS frontend

## Token Rules (CRITICAL — enforced every turn)
- Read ONLY files needed for the immediate task. Never index directories or read `node_modules/`, `.next/`, `venv/`, `*.csv`, `*.pdf` unless commanded.
- Output ONLY changed snippets or diffs — never full files unless asked.
- After each discrete feature or bug fix, stop and prompt: "Run /compact before next task."
- No conversational filler. Plan → Code → Verify. Nothing else.
- Max 2 fix attempts before stopping and asking the user what to try next.

## Code Graph (auto-loaded at session start)
Both files below are injected into context automatically — no need to ask Claude to read them.
- Never read raw source files to answer structural questions; use the graph data already in context.
- After modifying any code file the graph is rebuilt automatically by the PostToolUse hook.

@graphify-out/GRAPH_REPORT.md
@graphify-out/wiki/index.md

## Workflow
- Tasks >2 steps: write plan to `tasks/todo.md`, await approval, then execute.
- One objective at a time. Log unrelated bugs to `tasks/todo.md` — do NOT fix mid-task.
- Never mark done without running tests or diffing behavior. Show proof.
- After any user correction, log the pattern in `tasks/lessons.md`. Review at session start.

## Skills — load ONLY when task type matches. No preloading.
| Task Type | Skill to Load |
|---|---|
| UI / layout / components | `/mnt/skills/user/frontend-design/SKILL.md` |
| New feature (design phase) | `/mnt/skills/user/brainstorming/SKILL.md` |
| Writing implementation spec | `/mnt/skills/user/writing-plans/SKILL.md` |
| Executing a written plan | `/mnt/skills/user/executing-plans/SKILL.md` |
| 2+ independent parallel tasks | `/mnt/skills/user/dispatching-parallel-agents/SKILL.md` |
| Bug investigation | `/mnt/skills/user/systematic-debugging/SKILL.md` |
| New feature (implementation) | `/mnt/skills/user/test-driven-development/SKILL.md` |
| About to claim task complete | `/mnt/skills/user/verification-before-completion/SKILL.md` |
| Word / PDF / Excel output | `/mnt/skills/public/{docx|pdf|xlsx}/SKILL.md` |
