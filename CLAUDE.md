# Claude Code Configuration - RuFlo V3

## Stack
Vercel (serverless) · Supabase (DB) · Python/JS frontend

## Token Efficiency Rules (CRITICAL — enforced every turn)

- Read ONLY files needed for the immediate task — never index directories, `node_modules/`, `.next/`, `venv/`, `*.csv`, `*.pdf` unless commanded
- Output ONLY changed snippets or diffs — never full files unless asked
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

## Skill Loading (load ONLY when task type matches — no preloading)

| Task Type | Skill |
|---|---|
| UI / layout / components | `/mnt/skills/user/frontend-design/SKILL.md` |
| New feature (design phase) | `/mnt/skills/user/brainstorming/SKILL.md` |
| Writing implementation spec | `/mnt/skills/user/writing-plans/SKILL.md` |
| Executing a written plan | `/mnt/skills/user/executing-plans/SKILL.md` |
| 2+ independent parallel tasks | `/mnt/skills/user/dispatching-parallel-agents/SKILL.md` |
| Bug investigation | `/mnt/skills/user/systematic-debugging/SKILL.md` |
| New feature (implementation) | `/mnt/skills/user/test-driven-development/SKILL.md` |
| About to claim task complete | `/mnt/skills/user/verification-before-completion/SKILL.md` |
| Word / PDF / Excel output | `/mnt/skills/public/{docx\|pdf\|xlsx}/SKILL.md` |

## Behavioral Rules (Always Enforced)

- Do what has been asked; nothing more, nothing less
- NEVER create files unless they're absolutely necessary for achieving your goal
- ALWAYS prefer editing an existing file to creating a new one
- NEVER proactively create documentation files (*.md) or README files unless explicitly requested
- NEVER save working files, text/mds, or tests to the root folder
- Never continuously check status after spawning a swarm — wait for results
- ALWAYS read a file before editing it
- NEVER commit secrets, credentials, or .env files

## File Organization

- NEVER save to root folder — use the directories below
- Use `/src` for source code files
- Use `/tests` for test files
- Use `/docs` for documentation and markdown files
- Use `/config` for configuration files
- Use `/scripts` for utility scripts
- Use `/examples` for example code

## Project Architecture

- Follow Domain-Driven Design with bounded contexts
- Use typed interfaces for all public APIs
- Prefer TDD London School (mock-first) for new code
- Use event sourcing for state changes
- Ensure input validation at system boundaries

### Project Config

- **Topology**: hierarchical-mesh
- **Max Agents**: 15
- **Memory**: hybrid
- **HNSW**: Enabled
- **Neural**: Enabled

## Build & Test

```bash
# Build
npm run build

# Test
npm test

# Lint
npm run lint
```

- ALWAYS run tests after making code changes
- ALWAYS verify build succeeds before committing

## Security Rules

- NEVER hardcode API keys, secrets, or credentials in source files
- NEVER commit .env files or any file containing secrets
- Always validate user input at system boundaries
- Always sanitize file paths to prevent directory traversal
- Run `npx @claude-flow/cli@latest security scan` after security-related changes

## Concurrency: 1 MESSAGE = ALL RELATED OPERATIONS

- All operations MUST be concurrent/parallel in a single message
- Use Claude Code's Agent tool for spawning agents, not just MCP
- ALWAYS spawn ALL agents in ONE message with full instructions via Agent tool
- ALWAYS batch ALL file reads/writes/edits in ONE message
- ALWAYS batch ALL Bash commands in ONE message

## Swarm Orchestration

- MUST initialize the swarm using CLI tools when starting complex tasks
- MUST spawn concurrent agents using Claude Code's Agent tool
- Never use CLI tools alone for execution — Agent tool agents do the actual work
- MUST call CLI tools AND Agent tool in ONE message for complex work

### 3-Tier Model Routing (ADR-026)

| Tier | Handler | Latency | Cost | Use Cases |
|------|---------|---------|------|-----------|
| **1** | Agent Booster (WASM) | <1ms | $0 | Simple transforms (var→const, add types) — Skip LLM |
| **2** | Haiku | ~500ms | $0.0002 | Simple tasks, low complexity (<30%) |
| **3** | Sonnet/Opus | 2-5s | $0.003-0.015 | Complex reasoning, architecture, security (>30%) |

- For Tier 1 simple transforms, use Edit tool directly — no LLM agent needed

## Swarm Configuration & Anti-Drift

- ALWAYS use hierarchical topology for coding swarms
- Keep maxAgents at 6-8 for tight coordination
- Use specialized strategy for clear role boundaries
- Use `raft` consensus for hive-mind (leader maintains authoritative state)
- Run frequent checkpoints via `post-task` hooks
- Keep shared memory namespace for all agents

## Swarm Execution Rules

- ALWAYS use `run_in_background: true` for all Agent tool calls
- ALWAYS put ALL Agent calls in ONE message for parallel execution
- After spawning, STOP — do NOT add more tool calls or check status
- Never poll agent status repeatedly — trust agents to return
- When agent results arrive, review ALL results before proceeding
