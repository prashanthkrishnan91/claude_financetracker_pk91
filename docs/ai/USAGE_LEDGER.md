# AI Usage Ledger

Committed, sanitized audit trail of Claude token/cost usage by PR, prompt, and session.

## Purpose

A future auditor can pull this file from GitHub and understand token/cost burn by PR/prompt/session without needing local raw snapshots. Raw `.ai/usage/*.json` files stay local and gitignored.

## Privacy rule

Never commit to this ledger:
- Raw `.ai/usage/*.json` snapshots
- Prompts or conversation content
- Secrets, env values, or API keys
- Local Claude DB data (`~/.claude/`)

This file contains only sanitized session-level and prompt-level summaries.

## Two-layer model

| Layer | Location | Committed? | Purpose |
|---|---|---|---|
| Raw snapshots | `.ai/usage/*.json` | No (gitignored) | Local debugging, full token detail |
| Sanitized ledger | `docs/ai/USAGE_LEDGER.md` | Yes | Auditable PR/prompt/session-level history |

PR usage notes in the PR body are not sufficient for workflow audits — they are too lossy once the PR is merged. This ledger is the durable audit source.

## Ledger columns (26)

| Column | Description |
|---|---|
| Date | ISO date of the session (YYYY-MM-DD) |
| PR | PR number or `unknown` |
| Prompt ID | Short identifier for this prompt within the PR (e.g. `p01`, `audit-01`, or `unknown`) |
| Phase | `initial`, `follow-up`, `audit`, `merge-gate`, `backfill`, or `unknown` |
| Linked PR | For follow-up patches: the original PR number. Otherwise `n/a`. |
| Repo area | e.g. `workflow/docs`, `backend/portfolio`, `frontend/dashboard` |
| Claude session | Session URL or `unknown` |
| Model | e.g. `claude-sonnet-4-6`, `claude-opus-4-7` |
| Chat strategy | `same-chat`, `new-chat`, or `unknown` |
| Source | `ccusage`, `statusline`, `manual`, or `unavailable` |
| Input tok | Cumulative input tokens at snapshot time, or `unavailable` |
| Output tok | Cumulative output tokens at snapshot time, or `unavailable` |
| Cache read | Cumulative cache read tokens, or `unavailable` |
| Cache creation | Cumulative cache creation tokens, or `unavailable` |
| Total tok | Cumulative total tokens, or `unavailable` |
| Est. cost | Cumulative estimated cost, or `unavailable` |
| Δ input | Per-prompt input token delta (current minus baseline), or `unavailable` |
| Δ output | Per-prompt output token delta, or `unavailable` |
| Δ cache read | Per-prompt cache read delta, or `unavailable` |
| Δ cache creation | Per-prompt cache creation delta, or `unavailable` |
| Δ total | Per-prompt total token delta, or `unavailable` |
| Δ cost | Per-prompt cost delta, or `unavailable` |
| Waste | `none`, `preventable-follow-up`, `necessary-follow-up`, `exploration`, or `unknown` |
| Main drivers | What consumed tokens (e.g. broad discovery, many iterations) |
| Follow-up patches | Number of follow-up PRs required |
| Efficiency lesson | One-line lesson for future sessions |

## Ledger table

| Date | PR | Prompt ID | Phase | Linked PR | Repo area | Session | Model | Chat | Source | Input tok | Output tok | Cache read | Cache creation | Total tok | Est. cost | Δ input | Δ output | Δ cache read | Δ cache creation | Δ total | Δ cost | Waste | Main drivers | Follow-up patches | Efficiency lesson |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| YYYY-MM-DD | #000 | p01 | initial | n/a | workflow/docs | unknown | claude-sonnet-4-6 | same-chat | manual | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | none | template row — replace with real data | 0 | n/a |

## Adding a row for the current PR

### Coarse mode (session totals only)

```bash
bash scripts/ai/usage_snapshot.sh \
  --pr <PR-number> \
  --prompt-id p01 \
  --phase initial \
  --model <model-name> \
  --chat-strategy same-chat \
  --repo-area "workflow/docs" \
  --main-drivers "anchor reads, file writes" \
  --follow-up-patches 0 \
  --waste-classification none \
  --efficiency-lesson "narrow anchor reads next time" \
  --append-ledger
```

### Per-prompt delta mode (recommended)

```bash
# a) Before starting Claude work — save a baseline:
bash scripts/ai/usage_snapshot.sh --save-baseline before-pr123

# b) After Claude completes the prompt — capture snapshot with delta:
bash scripts/ai/usage_snapshot.sh \
  --pr 123 \
  --prompt-id p01 \
  --phase initial \
  --model claude-sonnet-4-6 \
  --chat-strategy same-chat \
  --repo-area "workflow/docs" \
  --waste-classification none \
  --delta-from-baseline .ai/usage/baseline-before-pr123.json \
  --append-ledger

# c) For a follow-up patch on the same PR:
bash scripts/ai/usage_snapshot.sh --save-baseline before-pr123-p02
bash scripts/ai/usage_snapshot.sh \
  --pr 123 \
  --prompt-id p02 \
  --phase follow-up \
  --linked-pr 123 \
  --waste-classification preventable-follow-up \
  --delta-from-baseline .ai/usage/baseline-before-pr123-p02.json \
  --append-ledger
```

If ccusage is unavailable, token fields show `unavailable` — that is acceptable. Accurate unknowns are better than false values.

## Backfilling prior sessions

When exact PR mapping is unknown:

```bash
bash scripts/ai/backfill_usage_ledger.sh --since YYYY-MM-DD
```

Prints 26-column candidate rows with `phase=backfill`, `unknown` PR mapping, and `unavailable` deltas. Append only with `--append-ledger`.
Do not guess PR numbers — mark as `unknown`.

## Audit guidance

Use this ledger plus GitHub PR history to diagnose token burn:
- High Δ total for follow-up prompts → preventable rework; check waste classification.
- High input tokens, low output → over-broad discovery reads.
- High follow-up patches → unclear contracts or scope at PR time.
- Recurring efficiency lessons → candidate for `docs/ai/MISS_LEDGER.md` promotion.
| 2026-05-13 | 304 | p01 | unknown | n/a | unknown | unknown | unknown | unknown | ccusage | 110 | 37631 | 8117991 | 470732 | 8626464 | $7.9423955 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unknown | unknown | 0 | n/a |
| 2026-05-14 | unknown | arch-memo-01 | audit | n/a | docs/architecture | unknown | claude-opus-4-7 | new-chat | ccusage | 1695 | 3859 | 1336063 | 241079 | 1582696 | $1.14597485 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | none | anchor reads, code exploration agents, architecture memo | 0 | delegated code mapping to parallel Explore agents to protect context |
| 2026-05-14 | unknown | p01 | initial | n/a | backend/intel-v3 | unknown | claude-opus-4-7 | new-chat | ccusage | 22681 | 55704 | 10383036 | 203484 | 10664905 | $7.969297999999999 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | none | anchor reads, code exploration, seam + tests | 0 | narrow anchor reads; surgical seam swap |
| 2026-05-14 | unknown | p01 | initial | n/a | backend/intel-v3 | unknown | claude-opus-4-7 | new-chat | ccusage | 82 | 79379 | 7578574 | 234815 | 7892850 | $7.2417657500000026 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | none | anchor reads, Stage 3.2 worker + job store + migration + tests | 0 | reused existing full-portfolio adapter; in-memory fake supabase for job-store tests |
| 2026-05-14 | 314 | p02 | follow-up | 314 | backend/intel-v3 | unknown | claude-opus-4-7 | same-chat | ccusage | 130 | 108662 | 13209385 | 737719 | 14055896 | $13.93263625 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | necessary-follow-up | terminal-state reopen logic, idempotency tests, Stage 3.1 test fragility fix | 1 | anchor patched evidence ages to the real clock when the code under test uses datetime.now() |
| 2026-05-14 | unknown | p01 | initial | n/a | workflow/railway | unknown | claude-haiku-4-5-20251001 | new-chat | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | none | single shared railway.toml with shell conditional on PROCESS_TYPE | 0 | narrow single-file fix; minimal scope |
| 2026-05-14 | 314 | p03 | follow-up | 314 | backend/intel-v3 | unknown | claude-opus-4-7 | same-chat | ccusage | 188 | 163333 | 22119761 | 1108834 | 23392116 | $22.074357999999997 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | necessary-follow-up | trace AgentOrchestrator persistence contract, fix worker readback ticker-casing mismatch, diagnostics + tests | 2 | readback verification must key on the durable id (run_id), never on request-side string equality like ticker casing |
| 2026-05-14 | unknown | p01 | initial | n/a | backend/intel-v3 | unknown | claude-opus-4-7 | same-chat | ccusage | 262 | 195535 | 36695823 | 1575948 | 38467568 | $33.0872715 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | none | worker loop interval env config + default 60s + loop-summary log + tests/docs | 0 | ship operability knobs (poll interval, per-poll log) with the worker, not after production confusion |
| 2026-05-14 | unknown | p01 | initial | n/a | backend/intel-v3 | unknown | claude-opus-4-7 | same-chat | ccusage | 328 | 240320 | 51508180 | 2063698 | 53812526 | $44.661842499999985 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | none | failed-job retry handoff fix: explicit-refresh make-due + stale-claim recovery + diagnostics + tests | 0 | an explicit user action must override internal backoff timers; separate explicit-refresh paths from automatic-retry paths |
