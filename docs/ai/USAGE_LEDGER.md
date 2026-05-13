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
