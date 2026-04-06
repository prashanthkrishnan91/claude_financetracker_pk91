# CLAUDE.md

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes -- don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests -- then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management
1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md`
7. **Capture progress**:  Update `tasks/progress_log.md`

## Available Internal Libraries

1. frontend-design
   - Purpose: UI/UX, layout, styling, Streamlit improvements

2. ui-ux-pro-max
   - Purpose: Design intelligence, database of palettes, styles, UX rules

3. superpowers
   - Purpose: business logic, calculations, optimizations

4. claude-mem
   - Purpose: persistence, session state, memory handling

5. awesome-claude-code
   - Purpose: patterns, architecture, best practices

6. get-shit-done
   - Purpose: Accomplish given tasks based on provided prompts

## Decision Framework

- UI task → use  front-end design to build production grade using ui-ux-pro-max-skill to fetch styles and color palettes while adhering to the UX rules
- Logic/calculation → use superpowers
- State/memory → use claude-mem
- Refactor/design → use awesome-claude-code
- Sequential thinking and prompt based responses - use get-shit-done

## Rules

- Never duplicate logic across modules
- Always prefer modular reusable code
- Production-grade output only
- ALWAYS use services layer

If a required function does not exist:
→ create it inside the appropriate service file
→ then call underlying library
