# AI Usage Tracking

Lightweight workflow for capturing Claude session usage in PR summaries and a committed audit ledger.
No product code is touched. All raw data is gitignored and stays local.

## Two-layer model

| Layer | Location | Committed? | Purpose |
|---|---|---|---|
| Raw snapshots | `.ai/usage/*.json` | No (gitignored) | Local debugging, full token detail |
| Sanitized ledger | `docs/ai/USAGE_LEDGER.md` | Yes | Auditable PR/prompt/session-level history |

**PR usage notes in the PR body are not sufficient for workflow audits.** They are too lossy once the PR is merged. The committed ledger (`docs/ai/USAGE_LEDGER.md`) is the durable audit source.

## Quick start (manual — preferred)

```bash
# Print usage note + ledger row (no file changes):
bash scripts/ai/usage_snapshot.sh --pr <PR-number> --model <model> --repo-area "area/stage"

# Per-prompt delta workflow:
bash scripts/ai/usage_snapshot.sh --save-baseline before-pr<N>   # before Claude work
bash scripts/ai/usage_snapshot.sh \
  --pr <PR-number> --prompt-id p01 --phase initial \
  --model <model> --repo-area "area/stage" \
  --main-drivers "anchor reads" --follow-up-patches 0 \
  --waste-classification none \
  --efficiency-lesson "narrow reads" \
  --delta-from-baseline .ai/usage/baseline-before-pr<N>.json \
  --append-ledger
```

Copy the printed `**Usage note:**` line into the PR body's **AI usage note** field.
Copy or confirm the `| ledger row |` was appended to `docs/ai/USAGE_LEDGER.md`.

This script is **not run automatically**. No network calls or package execution happen unless you invoke it.

## CLI flags

| Flag | Description |
|---|---|
| `--pr <number-or-url>` | PR number or URL (used in ledger row) |
| `--prompt-id <id>` | Short prompt identifier within this PR (e.g. `p01`, `audit-01`) |
| `--phase <phase>` | `initial`, `follow-up`, `audit`, `merge-gate`, `backfill`, or `unknown` |
| `--linked-pr <number>` | For follow-up patches: the original PR number |
| `--session-url <url>` | Claude session URL (used in ledger row) |
| `--model <name>` | Model name (e.g. `claude-sonnet-4-6`) |
| `--chat-strategy <value>` | `same-chat`, `new-chat`, or `unknown` |
| `--repo-area <text>` | Repo area / stage (e.g. `workflow/docs`) |
| `--main-drivers <text>` | What consumed tokens |
| `--follow-up-patches <n>` | Number of follow-up patches required |
| `--efficiency-lesson <text>` | One-line efficiency lesson |
| `--waste-classification <v>` | `none`, `preventable-follow-up`, `necessary-follow-up`, `exploration`, or `unknown` |
| `--append-ledger` | Append sanitized row to `docs/ai/USAGE_LEDGER.md` |
| `--save-baseline <name>` | Save current cumulative totals to `.ai/usage/baseline-<name>.json` |
| `--delta-from-baseline <path>` | Compute per-prompt token/cost deltas from a saved baseline file |
| `--help` | Print usage |

## How it works

1. Calls `npx ccusage@latest session --json` to read session token/cost data from the local Claude usage database (`~/.claude/`).
2. Normalizes all known ccusage JSON shapes (`{sessions:[...],totals:{...}}`, `{data:[...],summary:{...}}`, single session object, bare array) using jq `norm_obj` / `sum_arr` functions so token fields are always extracted correctly.
3. Captures repo, branch, timestamp, and `git diff --stat` for context.
4. Writes a raw JSON snapshot to `.ai/usage/` using `jq -n --arg` (preferred) or `python3` env-var pass (fallback). If neither is available, snapshot writing is skipped and the usage note still prints.
5. When `--save-baseline <name>` is passed, saves the normalized totals to `.ai/usage/baseline-<name>.json` for later delta computation.
6. When `--delta-from-baseline <path>` is passed, computes per-prompt token/cost deltas (current minus baseline) for the six delta columns.
7. Prints a compact human-readable usage note to stdout for pasting into the PR body.
8. Prints a sanitized 26-column Markdown ledger row for pasting into (or appending to) `docs/ai/USAGE_LEDGER.md`.
9. When `--append-ledger` is passed, appends the ledger row directly to `docs/ai/USAGE_LEDGER.md`.

**JSON is never built by raw string interpolation** — values are passed as typed arguments to `jq` or as environment variables to `python3`.

## Fallback behaviour (fails soft)

| Missing tool | Behaviour |
|---|---|
| `npx` / Node not installed | `ccusage` skipped; source reported as `unavailable` |
| `ccusage session` returns no data | Source reported as `unavailable`; fallback hints printed |
| `jq` not installed | Falls back to `python3` for snapshot writing; delta/normalization unavailable |
| `jq` and `python3` both absent | Snapshot writing skipped; usage note and ledger row still printed |
| `.ai/usage/` not writable | Snapshot write silently skipped; note still printed |
| `docs/ai/USAGE_LEDGER.md` not found | `--append-ledger` warns and skips; row still printed to stdout |
| Baseline file not found | Delta columns show `unavailable`; snapshot still written |

Fallback options when ccusage is unavailable:
- Claude Code status line shows a live session token count — use that as `source: statusline`.
- Estimate from task scope (small/medium/large) as `source: manual`.
- Token fields in the ledger row will show `unavailable` — that is acceptable.

## Backfill

When you have recent Claude sessions without committed ledger rows:

```bash
bash scripts/ai/backfill_usage_ledger.sh --since YYYY-MM-DD
```

Prints 26-column candidate rows with `phase=backfill`, `unknown` PR mapping, and `unavailable` deltas. Use `--append-ledger` to append them.
Never guess PR numbers — mark unknown ones as `unknown`.

## Optional Stop hook (explicit opt-in only)

The repo's `.claude/settings.json` includes a `Stop` hook entry that runs `usage_snapshot.sh` **only** when `AI_USAGE_SNAPSHOT_ON_STOP=1` is set in your local environment. By default it is a no-op.

To enable automatic capture at session end:
```bash
export AI_USAGE_SNAPSHOT_ON_STOP=1   # add to your ~/.zshrc or ~/.bashrc
```

The hook:
- Writes to `.ai/usage/` only (gitignored)
- Does **not** add context to the Claude conversation
- Does **not** block the session
- Is completely silent if the env var is absent

To disable the hook entry entirely, remove the `usage_snapshot` command from `.claude/settings.json`.

## Limitations

- `ccusage` reads the local Claude usage DB (`~/.claude/`). Not available in CI, web-only Claude sessions, or machines without Claude Code CLI.
- Delta columns require running `--save-baseline` before and `--delta-from-baseline` after each prompt. Baselines are gitignored and local only.
- Cost figures from ccusage reflect API-level pricing and may differ from subscription billing.
- Raw snapshots are never committed. `.ai/usage/` is gitignored.

## Usage note format (required in every PR)

```
**Usage note:** Low/Medium/High; source: ccusage/statusline/manual/unavailable;
main drivers: [e.g. large context reads, many tool calls];
justified: yes/partially/no;
next efficiency improvement: [e.g. narrow anchor reads, skip redundant tool calls]
```

**Usage level guide:**
- Low — routine small patch, few tool calls, short context
- Medium — multi-file change, several discovery reads, moderate tool calls
- High — broad discovery, large diffs, many iterations
