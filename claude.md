# System Context & Project Architecture
- **Tech Stack Baseline**: Serverless deployments (Vercel), Database management (Supabase), and Python/frontend frameworks.
- **Universal Directive**: Maximize token efficiency. Be ruthlessly concise in all outputs and system operations.

## 1. Context & Token Management (CRITICAL)
- **Targeted Reads Only**: NEVER index or read entire directories, large data files (`*.csv`, `*.pdf`), or build folders (`node_modules/`, `venv/`, `.next/`) unless explicitly commanded. Read only the specific files required for the immediate task.
- **Concise Code Outputs**: When modifying files, do NOT output the entire file in the chat unless specifically requested. Use snippet replacements, exact line references, or git diff formats.
- **Proactive Compaction**: After completing a discrete feature or fixing a complex bug, immediately prompt the user to run `/compact` before moving to the next task.
- **Zero Filler**: Skip polite conversational filler. Deliver only the technical plan, the exact code modifications, or the error resolution.
- **Use code graph**: Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure. If graphify-out/wiki/index.md exists, navigate it instead of reading raw files. After modifying code files in this session, run python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))" to keep the graph current

## 2. Workflow Orchestration
- **Plan Mode Default**: For ANY non-trivial task (>2 steps or architectural changes), write a detailed, step-by-step spec upfront. Await approval before writing code to prevent expensive rewrites.
- **Isolated Execution**: Tackle one objective at a time. If unrelated bugs or refactor opportunities are discovered, do NOT fix them immediately. Log them in `tasks/todo.md` to prevent context drift.
- **Strict Subagent Restraint**: Use subagents ONLY for highly isolated, complex research or parallel analysis. Do not use them for standard coding tasks, as this multiplies context windows.

## 3. Development Rules & Execution
- **Modular & Reusable**: Never duplicate logic. Always utilize the established services layer. If a required function does not exist, create it inside the appropriate service file first, then call the underlying library.
- **Verification Before Done**: Never mark a task complete without proving it works. Run tests, check logs, and diff behavior between main and your changes.
- **Autonomous Bug Fixing**: When given a bug report, point at the logs, errors, or failing tests and resolve them. Do not ask for hand-holding.
- **Demand Elegance**: For non-trivial changes, pause and ask "Is there a more elegant way?" If a fix feels hacky, implement the elegant solution. Skip this for simple, obvious fixes to avoid over-engineering.

## 4. Task Management
- **Plan First**: Write the plan to `tasks/todo.md` with checkable items.
- **Verify Plan**: Check in before starting implementation.
- **Track Progress**: Mark items complete as you go.
- **Explain Changes**: Provide a high-level, 1-2 sentence summary at each step.
- **Document Results**: Add a review section to `tasks/todo.md` and update `tasks/progress_log.md`.
- **Self-Improvement Loop**: After ANY correction from the user, immediately update `tasks/lessons.md` with the pattern to prevent repeating the same mistake. Review this file at session start.

## 5. Decision Framework & Internal Skills
- **UI/UX Tasks**: Use `frontend-design` to build production-grade interfaces. Use `ui-ux-pro-max` to fetch styles, palettes, and adhere to UX rules.
- **Logic & Calculations**: Use `superpowers`.
- **State & Memory Handling**: Use `claude-mem`.
- **Architecture & Refactoring**: Use `awesome-claude-code`.
- **Sequential Execution**: Use `get-shit-done` for structured, spec-driven workflow execution.
