# Portfolio Intelligence Platform

A lean personal portfolio tool with exactly three views:

1. **Positions** — every holding with cost basis by tax lot, unrealized gain/loss, and
   short-term vs long-term tax status including a days-until-long-term countdown per lot.
2. **Recommendations** — current Buy/Hold/Trim/Sell calls from the deterministic Intel v3
   policy engine, each with a one-line rationale showing its work (profit threshold,
   estimated tax impact at the configured bracket, allocation drift). A recommendation
   with no rationale never renders.
3. **Watchlist** — user-defined candidate tickers with user-defined price criteria,
   flagged when the criteria are met. The app surfaces candidates; it never picks stocks.

Deterministic, auditable decisions backed by sourced evidence, with a plain-English UI
built for an amateur investor — not a quant terminal. See `REFACTOR_REPORT.md` for the
refactor that produced this shape.

---

## Architecture

```
Next.js 14 (Vercel)  ──▶  FastAPI (Railway)  ──▶  Supabase (PostgreSQL)
     │                            │
     │ React Query                │ async/await
     │ Supabase Auth (JWT)        │ Plaid, Alpaca, yfinance, SEC EDGAR
     │ Recharts                   │ AES-256-GCM for at-rest API keys
     │ Tailwind + shadcn/ui       │ Deterministic Intel v3 policy
     ▼                            ▼
  Mobile-first UI             Research artifacts (backend-only)
```

Visible Buy/Hold/Trim/Sell authority is owned by the **deterministic Intel v3 backend policy**. LLMs, agents, and research workers may produce sourced artifacts but never own final visible action authority.

See [`v2/docs/architecture.md`](v2/docs/architecture.md) for detailed system design and [`docs/product/NORTH_STAR.md`](docs/product/NORTH_STAR.md) for product direction.

---

## Repository layout

```
.
├── v2/
│   ├── backend/              # FastAPI + Supabase (Python)
│   │   ├── app/              # routers, services, models, middleware
│   │   ├── tests/            # pytest suite
│   │   ├── migrations/       # incremental SQL (008+)
│   │   ├── requirements.txt
│   │   ├── Procfile          # Railway
│   │   └── railway.toml
│   ├── frontend/             # Next.js 14 + Tailwind + shadcn/ui
│   │   ├── src/              # app router, components, lib, types
│   │   ├── package.json
│   │   └── vercel.json
│   ├── database/             # Canonical PostgreSQL schema (001–017)
│   ├── docs/                 # Architecture
│   ├── README.md
│   └── progress_log.md
│
├── docs/
│   ├── ai/                   # AI Repo Operating System (v4) + workflow guidance
│   │   ├── HANDOFF.md
│   │   ├── REPO_HYGIENE.md   # repo cleanup rules + audit script how-to
│   │   ├── AI_REPO_OPERATING_SYSTEM.md
│   │   ├── SAFETY_PACKS_AND_ARCHETYPES.md
│   │   └── ...
│   └── product/              # NORTH_STAR, ROADMAP, BUILD_QUEUE, DECISION_LOG, ...
│
├── artifacts/                # Long-form Intel v3 architecture plans (PDF + .md)
├── scripts/
│   └── repo_hygiene/         # audit_repo_hygiene.py — see docs/ai/REPO_HYGIENE.md
├── .claude/                  # Claude Code skills, agents, commands, hooks
├── AGENTS.md                 # Portable entry point for Claude / Codex / ChatGPT
├── CLAUDE.md                 # Claude Code workflow rules
└── README.md
```

---

## Local development

### Backend (FastAPI)

```bash
cd v2/backend
cp .env.example .env             # fill in Supabase + provider keys
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs` when `DEBUG=true`.

### Frontend (Next.js)

```bash
cd v2/frontend
cp .env.local.example .env.local # fill in Supabase URL + anon key + API URL
npm install
npm run dev
```

Open `http://localhost:3000`.

### Database

Apply SQL files in order from [`v2/database/`](v2/database/) (001 → 025) and any newer files in [`v2/backend/migrations/`](v2/backend/migrations/) using the Supabase SQL editor. `025_watchlist.sql` is required for the Watchlist view.

---

## Deployment

- **Frontend:** Vercel — see [`v2/frontend/vercel.json`](v2/frontend/vercel.json).
- **Backend:** Railway — see [`v2/backend/railway.toml`](v2/backend/railway.toml) and `Procfile` (`uvicorn app.main:app`).
- **Database:** Supabase (PostgreSQL + Auth + RLS).

---

## Tests

```bash
cd v2/backend && pytest                    # backend regression suite
cd v2/frontend && npm test                 # frontend Jest suite
cd v2/frontend && npm run lint             # eslint
cd v2/frontend && npx tsc --noEmit         # typecheck
```

Test tier guidance lives in [`docs/ai/TEST_ROUTING.md`](docs/ai/TEST_ROUTING.md).

---

## Repo hygiene

Run before opening a non-trivial PR:

```bash
python3 scripts/repo_hygiene/audit_repo_hygiene.py
```

Read-only audit. Reports stale legacy patterns, oversized progress logs, and skip/xfail tests. Rules and exception process: [`docs/ai/REPO_HYGIENE.md`](docs/ai/REPO_HYGIENE.md).

---

## Workflow

For any non-trivial change, follow the AI Repo Operating System v4 (`docs/ai/AI_REPO_OPERATING_SYSTEM.md`) and the rules in [`CLAUDE.md`](CLAUDE.md). Current state lives in [`docs/ai/HANDOFF.md`](docs/ai/HANDOFF.md).
