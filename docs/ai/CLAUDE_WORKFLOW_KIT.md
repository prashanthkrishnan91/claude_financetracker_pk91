# Claude Workflow Kit — Investing App

Purpose: reduce Claude token burn by turning repeated context into stable repo knowledge and forcing small, targeted prompts.

## Core rule

Never ask Claude to re-understand the entire portfolio system.
Always provide a compact state pack and target a small surface.

## Canonical stack differences vs travel app

IMPORTANT: this app is NOT identical to travel app.

- Next.js 14.2.35 (not 15)
- React 18 (not 19)
- Tailwind 3 (not 4)

Claude must not assume shared patterns without verification.

## Hot surfaces

### Intel (reasoning)

- backend/app/services/recommendation_engine.py
- backend/app/services/portfolio_advisor.py
- backend/app/routes/recommendations.py
- backend/app/models/

### Deploy (allocation)

- allocation logic in backend services
- frontend components under v2/frontend

### Analytics / decision log

- backend/app/routes/analytics
- Supabase tables: decision_log, recommendations, agent_runs, agent_insights

## Prompt shell

```md
Repo: prashanthkrishnan91/claude_financetracker_pk91
Mode: focused implementation
Model: Sonnet for feature, Codex for bug/audit
Chat: new chat

Task:
[one paragraph]

Expected behavior:
[one paragraph]

Actual behavior:
[one paragraph]

State pack:
[logs, API response, DB schema snippet]

Relevant surfaces:
- [file]
- [file]

Constraints:
- Do not rebuild entire pipeline.
- Preserve schema compatibility.
- No LLM usage for decision_log feature.

Deliverables:
1. Root cause
2. Files changed
3. Tests run
4. PR summary (Supabase SQL required / not required)
```

## Model usage rules

- Codex: debugging, refactors, schema fixes
- Sonnet: feature builds (default)
- Opus: planning only, produce spec, then stop

## Critical token-saving rules

- Never paste full files into Claude unless absolutely required
- Never ask Claude to "scan the repo"
- Always provide logs instead of asking Claude to infer
- Always start a new chat for new feature or fix
- Keep prompts under 300–500 words whenever possible

## Maintenance

Update docs/ai/HANDOFF.md after every meaningful change.
