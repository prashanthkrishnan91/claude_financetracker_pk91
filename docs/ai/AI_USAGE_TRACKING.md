# AI Usage Tracking

Lightweight workflow for capturing Claude session usage in PR summaries and a committed audit ledger.
No product code is touched. All raw data is gitignored and stays local.

## Two-layer model

| Layer | Location | Committed? | Purpose |
|---|---|---|---|
| Raw snapshots | `.ai/usage/*.json` | No (gitignored) | Local debugging, full token detail |
| Sanitized ledger | `docs/ai/USAGE_LEDGER.md` | Yes | Auditable PR/prompt/session-level history |

**PR usage notes in the PR body are not sufficient for workflow audits.** They are too lossy once the PR is merged. The committed ledger (`docs/ai/USAGE_LEDGER.md`) is the durable audit source.

## Ledger claim enforcement

The readiness checker (`scripts/workflow/ai_pr_readiness_check.py`) enforces that PR body usage claims match committed ledger state:

- If a PR body says "usage tracked", "usage ledger updated", or "see usage ledger" but `docs/ai/USAGE_LEDGER.md` did not change in the PR, the checker hard-fails.
- If `docs/ai/USAGE_LEDGER.md` is unchanged and usage is not explicitly marked unavailable with a reason, Level 1+ PRs hard-fail.
- If tooling is unavailable, a manual row is still required in `docs/ai/USAGE_LEDGER.md` with metadata fields filled and token/delta fields marked `unavailable`.
- Exact per-prompt deltas require saving a baseline before work. If the baseline was missed, mark delta fields `unavailable` honestly — do not fabricate values.
- Same-chat continuation must be reflected in the ledger row with `chat: same-chat`.
- The readiness check runs in CI (`.github/workflows/ai-pr-readiness.yml`) and locally via `python3 scripts/workflow/ai_pr_readiness_check.py`.

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
  --delta-from-baseline .ai/usage/baseline-before-pr<N>.json \
  --append-ledger
```

Copy the printed `**Usage note:**` line into the PR body's **AI usage note** field.

This script is **not run automatically**. No network calls or package execution happen unless you invoke it.

## CLI flags

| Flag | Description |
|---|---|
| `--pr <number-or-url>` | PR number or URL |
| `--prompt-id <id>` | Short prompt identifier (e.g. `p01`, `audit-01`) |
| `--phase <phase>` | `initial`, `follow-up`, `audit`, `merge-gate`, `backfill`, or `unknown` |
| `--linked-pr <number>` | For follow-up patches: the original PR number |
| `--session-url <url>` | Claude session URL |
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

## Fallback behaviour (fails soft)

| Missing tool | Behaviour |
|---|---|
| `npx` / Node not installed | `ccusage` skipped; source reported as `unavailable` |
| `ccusage session` returns no data | Source reported as `unavailable`; fallback hints printed |
| `jq` not installed | Falls back to `python3`; delta/normalization unavailable |
| `jq` and `python3` both absent | Snapshot writing skipped; usage note and ledger row still printed |
| `.ai/usage/` not writable | Snapshot write silently skipped; note still printed |
| `docs/ai/USAGE_LEDGER.md` not found | `--append-ledger` warns and skips; row still printed to stdout |
| Baseline file not found | Delta columns show `unavailable`; snapshot still written |

Fallback options when ccusage is unavailable:
- Claude Code status line shows a live session token count — use that as `source: statusline`.
- Estimate from task scope (small/medium/large) as `source: manual`.
- Token fields in the ledger row will show `unavailable` — that is acceptable.

## Limitations

- `ccusage` reads the local Claude usage DB (`~/.claude/`). Not available in CI, web-only Claude sessions, or machines without Claude Code CLI.
- Delta columns require running `--save-baseline` before and `--delta-from-baseline` after each prompt.
- Cost figures from ccusage reflect API-level pricing and may differ from subscription billing.
- Raw snapshots are never committed. `.ai/usage/` is gitignored.

## Usage note format (required in every PR)

```
**Usage note:** Low/Medium/High; source: ccusage/statusline/manual/unavailable;
main drivers: [e.g. large context reads, many tool calls];
justified: yes/partially/no;
next efficiency improvement: [e.g. narrow anchor reads, skip redundant tool calls]
```
