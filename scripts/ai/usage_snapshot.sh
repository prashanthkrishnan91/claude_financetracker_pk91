#!/usr/bin/env bash
# scripts/ai/usage_snapshot.sh
#
# Manual command: capture a lightweight AI usage snapshot before opening a PR.
# Run: bash scripts/ai/usage_snapshot.sh [OPTIONS]
#
# What it does:
# - Calls `npx ccusage@latest session --json` for session token/cost data.
# - Normalizes all known ccusage JSON shapes (array, {sessions}, {data},
#   {totals}, single object) so token fields are always extracted correctly.
# - Falls back gracefully if ccusage, npx, jq, or python3 are unavailable.
# - Writes a safe JSON snapshot to .ai/usage/ (gitignored, never committed).
#   JSON is built via `jq -n --arg` (preferred) or python3 env-var pass (fallback).
#   If neither is available, snapshot writing is skipped; the usage note still prints.
# - Prints a compact usage note to paste into the PR body.
# - Prints a sanitized 26-column Markdown ledger row for docs/ai/USAGE_LEDGER.md.
# - With --append-ledger, appends the ledger row to docs/ai/USAGE_LEDGER.md.
# - With --save-baseline <name>, saves current cumulative totals as a baseline
#   to .ai/usage/baseline-<name>.json for later delta computation.
# - With --delta-from-baseline <path>, computes per-prompt token/cost deltas.
#
# CLI flags:
#   --pr <number-or-url>          PR number or URL (ledger row)
#   --prompt-id <id>              Short prompt identifier (e.g. p01, audit-01)
#   --phase <phase>               initial | follow-up | audit | merge-gate | backfill | unknown
#   --linked-pr <number>          Linked PR if this is a follow-up
#   --session-url <url>           Claude session URL (ledger row)
#   --model <name>                Model name (e.g. claude-sonnet-4-6)
#   --chat-strategy <value>       same-chat | new-chat | unknown
#   --repo-area <text>            Repo area/stage (e.g. workflow/docs)
#   --main-drivers <text>         What consumed tokens
#   --follow-up-patches <n>       Number of follow-up patches required
#   --efficiency-lesson <t>       One-line efficiency lesson
#   --waste-classification <v>    none | preventable-follow-up | necessary-follow-up | exploration | unknown
#   --append-ledger               Append row to docs/ai/USAGE_LEDGER.md
#   --save-baseline <name>        Save current totals to .ai/usage/baseline-<name>.json
#   --delta-from-baseline <path>  Path to a baseline JSON; compute delta columns
#   --help                        Print this help
#
# This script is NOT run automatically. It is a manual step before opening a PR.

# -- Defaults ------------------------------------------------------------------
OPT_PR="unknown"
OPT_PROMPT_ID="unknown"
OPT_PHASE="unknown"
OPT_LINKED_PR="n/a"
OPT_SESSION_URL="unknown"
OPT_MODEL="unknown"
OPT_CHAT_STRATEGY="unknown"
OPT_REPO_AREA="unknown"
OPT_MAIN_DRIVERS="unknown"
OPT_FOLLOW_UP_PATCHES="0"
OPT_EFFICIENCY_LESSON="n/a"
OPT_WASTE_CLASSIFICATION="unknown"
OPT_APPEND_LEDGER=false
OPT_SAVE_BASELINE=""
OPT_DELTA_FROM_BASELINE=""

# -- Argument parsing ----------------------------------------------------------
print_help() {
  printf 'Usage: bash scripts/ai/usage_snapshot.sh [OPTIONS]\n\n'
  printf 'Options:\n'
  printf '  --pr <number-or-url>          PR number or URL (ledger row)\n'
  printf '  --prompt-id <id>              Short prompt identifier (e.g. p01, audit-01)\n'
  printf '  --phase <phase>               initial | follow-up | audit | merge-gate | backfill | unknown\n'
  printf '  --linked-pr <number>          Linked PR if this is a follow-up\n'
  printf '  --session-url <url>           Claude session URL (ledger row)\n'
  printf '  --model <name>                Model name (e.g. claude-sonnet-4-6)\n'
  printf '  --chat-strategy <value>       same-chat | new-chat | unknown\n'
  printf '  --repo-area <text>            Repo area/stage (e.g. workflow/docs)\n'
  printf '  --main-drivers <text>         What consumed tokens\n'
  printf '  --follow-up-patches <n>       Number of follow-up patches\n'
  printf '  --efficiency-lesson <t>       One-line efficiency lesson\n'
  printf '  --waste-classification <v>    none | preventable-follow-up | necessary-follow-up | exploration | unknown\n'
  printf '  --append-ledger               Append row to docs/ai/USAGE_LEDGER.md\n'
  printf '  --save-baseline <name>        Save current totals as a named baseline\n'
  printf '  --delta-from-baseline <path>  Compute delta from saved baseline file\n'
  printf '  --help                        Print this help\n'
  exit 0
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pr)                     OPT_PR="${2:-unknown}"; shift 2 ;;
    --prompt-id)              OPT_PROMPT_ID="${2:-unknown}"; shift 2 ;;
    --phase)                  OPT_PHASE="${2:-unknown}"; shift 2 ;;
    --linked-pr)              OPT_LINKED_PR="${2:-n/a}"; shift 2 ;;
    --session-url)            OPT_SESSION_URL="${2:-unknown}"; shift 2 ;;
    --model)                  OPT_MODEL="${2:-unknown}"; shift 2 ;;
    --chat-strategy)          OPT_CHAT_STRATEGY="${2:-unknown}"; shift 2 ;;
    --repo-area)              OPT_REPO_AREA="${2:-unknown}"; shift 2 ;;
    --main-drivers)           OPT_MAIN_DRIVERS="${2:-unknown}"; shift 2 ;;
    --follow-up-patches)      OPT_FOLLOW_UP_PATCHES="${2:-0}"; shift 2 ;;
    --efficiency-lesson)      OPT_EFFICIENCY_LESSON="${2:-n/a}"; shift 2 ;;
    --waste-classification)   OPT_WASTE_CLASSIFICATION="${2:-unknown}"; shift 2 ;;
    --append-ledger)          OPT_APPEND_LEDGER=true; shift ;;
    --save-baseline)          OPT_SAVE_BASELINE="${2:-}"; shift 2 ;;
    --delta-from-baseline)    OPT_DELTA_FROM_BASELINE="${2:-}"; shift 2 ;;
    --help|-h)                print_help ;;
    *) printf 'Unknown flag: %s\nRun with --help for usage.\n' "$1" >&2; exit 1 ;;
  esac
done

# -- Environment ---------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SNAPSHOT_DIR="${CLAUDE_PROJECT_DIR:-.}/.ai/usage"
LEDGER_FILE="$REPO_ROOT/docs/ai/USAGE_LEDGER.md"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%s")
DATE_ONLY=$(date -u +"%Y-%m-%d" 2>/dev/null || echo "unknown")
REPO=$(git remote get-url origin 2>/dev/null | sed 's|.*/||;s|\.git$||' || echo "unknown")
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
DIFF_STATS=$(git diff --stat HEAD 2>/dev/null | tail -1 || echo "unavailable")
SAFE_TS=$(printf '%s' "$TIMESTAMP" | tr ':' '-')
SAFE_BRANCH=$(printf '%s' "$BRANCH" | tr '/' '-' | tr ' ' '_')
SNAPSHOT_FILE="$SNAPSHOT_DIR/${SAFE_TS}-${SAFE_BRANCH}.json"

mkdir -p "$SNAPSHOT_DIR" 2>/dev/null || true

# -- Attempt ccusage -----------------------------------------------------------
CCUSAGE_JSON=""
CCUSAGE_SOURCE="unavailable"

if command -v npx >/dev/null 2>&1; then
  CCUSAGE_JSON=$(npx ccusage@latest session --json 2>/dev/null || true)
  if [ -n "$CCUSAGE_JSON" ]; then
    CCUSAGE_SOURCE="ccusage"
  fi
fi

# -- Normalize ccusage JSON to a single totals object -------------------------
# Handles all known shapes:
#   bare array of sessions
#   {sessions: [...], totals: {...}}
#   {data: [...], summary: {...}}
#   single session object
_CCUSAGE_NORMALIZED=$(printf '%s\n' "$CCUSAGE_JSON" | jq -c '
  def norm_obj:
    { inputTokens:         (.inputTokens // 0),
      outputTokens:        (.outputTokens // 0),
      cacheReadTokens:     (.cacheReadTokens // 0),
      cacheCreationTokens: (.cacheCreationTokens // .cacheWriteTokens // 0),
      totalTokens:         (.totalTokens // 0),
      totalCost:           (.totalCost // .costUSD // 0) };
  def sum_arr:
    { inputTokens:         ([.[].inputTokens // 0] | add // 0),
      outputTokens:        ([.[].outputTokens // 0] | add // 0),
      cacheReadTokens:     ([.[].cacheReadTokens // 0] | add // 0),
      cacheCreationTokens: ([.[] | (.cacheCreationTokens // .cacheWriteTokens // 0)] | add // 0),
      totalTokens:         ([.[].totalTokens // 0] | add // 0),
      totalCost:           ([.[] | (.totalCost // .costUSD // 0)] | add // 0) };
  if   type == "array"  then sum_arr
  elif type == "object" then
    if   .totals   != null then (.totals   | norm_obj)
    elif .summary  != null then (.summary  | norm_obj)
    elif .sessions != null then (.sessions | sum_arr)
    elif .data     != null then (.data     | sum_arr)
    else norm_obj
    end
  else null
  end
' 2>/dev/null || echo "null")

# -- Save baseline if requested -----------------------------------------------
if [ -n "$OPT_SAVE_BASELINE" ]; then
  BASELINE_SAVE_PATH="$SNAPSHOT_DIR/baseline-${OPT_SAVE_BASELINE}.json"
  if [ "$_CCUSAGE_NORMALIZED" != "null" ] && command -v jq >/dev/null 2>&1; then
    printf '%s\n' "$_CCUSAGE_NORMALIZED" | jq --arg ts "$TIMESTAMP" '. + {_savedAt: $ts}' \
      > "$BASELINE_SAVE_PATH" 2>/dev/null \
      && printf 'Baseline saved: %s\n' "$BASELINE_SAVE_PATH" \
      || printf 'WARNING: baseline save failed\n' >&2
  else
    printf 'WARNING: ccusage data unavailable — baseline not saved\n' >&2
  fi
fi

# -- Compute delta if requested -----------------------------------------------
DELTA_INPUT="unavailable"
DELTA_OUTPUT="unavailable"
DELTA_CACHE_READ="unavailable"
DELTA_CACHE_CREATION="unavailable"
DELTA_TOTAL="unavailable"
DELTA_COST="unavailable"

if [ -n "$OPT_DELTA_FROM_BASELINE" ]; then
  if [ ! -f "$OPT_DELTA_FROM_BASELINE" ]; then
    printf 'WARNING: baseline file not found: %s\n' "$OPT_DELTA_FROM_BASELINE" >&2
  elif [ "$_CCUSAGE_NORMALIZED" = "null" ]; then
    printf 'WARNING: ccusage data unavailable — delta not computed\n' >&2
  elif command -v jq >/dev/null 2>&1; then
    _BASELINE_JSON=$(jq -c '.' "$OPT_DELTA_FROM_BASELINE" 2>/dev/null || echo "null")
    if [ "$_BASELINE_JSON" != "null" ]; then
      _DELTA=$(jq -n \
        --argjson cur  "$_CCUSAGE_NORMALIZED" \
        --argjson base "$_BASELINE_JSON" \
        '
        def diff(a; b):
          if (a | type) == "number" and (b | type) == "number" then a - b
          else "unavailable" end;
        {
          dIn:   diff($cur.inputTokens;         $base.inputTokens),
          dOut:  diff($cur.outputTokens;        $base.outputTokens),
          dCR:   diff($cur.cacheReadTokens;     $base.cacheReadTokens),
          dCC:   diff($cur.cacheCreationTokens; $base.cacheCreationTokens),
          dTot:  diff($cur.totalTokens;         $base.totalTokens),
          dCost: diff($cur.totalCost;           $base.totalCost)
        }
        ' 2>/dev/null || echo "null")
      if [ "$_DELTA" != "null" ]; then
        DELTA_INPUT=$(printf '%s' "$_DELTA" | jq -r '.dIn | tostring' 2>/dev/null || echo "unavailable")
        DELTA_OUTPUT=$(printf '%s' "$_DELTA" | jq -r '.dOut | tostring' 2>/dev/null || echo "unavailable")
        DELTA_CACHE_READ=$(printf '%s' "$_DELTA" | jq -r '.dCR | tostring' 2>/dev/null || echo "unavailable")
        DELTA_CACHE_CREATION=$(printf '%s' "$_DELTA" | jq -r '.dCC | tostring' 2>/dev/null || echo "unavailable")
        DELTA_TOTAL=$(printf '%s' "$_DELTA" | jq -r '.dTot | tostring' 2>/dev/null || echo "unavailable")
        DELTA_COST=$(printf '%s' "$_DELTA" | jq -r '.dCost | tostring' 2>/dev/null || echo "unavailable")
      fi
    fi
  fi
fi

# -- Write raw snapshot (gitignored, never committed) -------------------------
# JSON is never built by raw string interpolation.
SNAPSHOT_WRITTEN=false

if command -v jq >/dev/null 2>&1; then
  jq -n \
    --arg ts "$TIMESTAMP" \
    --arg repo "$REPO" \
    --arg branch "$BRANCH" \
    --arg diff_stats "$DIFF_STATS" \
    --arg ccusage_source "$CCUSAGE_SOURCE" \
    --argjson ccusage "${CCUSAGE_JSON:-null}" \
    '{timestamp:$ts,repo:$repo,branch:$branch,diff_stats:$diff_stats,ccusage_source:$ccusage_source,ccusage:$ccusage}' \
    > "$SNAPSHOT_FILE" 2>/dev/null && SNAPSHOT_WRITTEN=true
elif command -v python3 >/dev/null 2>&1; then
  _TS="$TIMESTAMP" _REPO="$REPO" _BRANCH="$BRANCH" \
  _DIFF="$DIFF_STATS" _SRC="$CCUSAGE_SOURCE" \
  python3 -c "
import json, os
print(json.dumps({
    'timestamp': os.environ['_TS'],
    'repo': os.environ['_REPO'],
    'branch': os.environ['_BRANCH'],
    'diff_stats': os.environ['_DIFF'],
    'ccusage_source': os.environ['_SRC'],
    'ccusage': None,
}, indent=2))
" > "$SNAPSHOT_FILE" 2>/dev/null && SNAPSHOT_WRITTEN=true
fi

# -- Extract token fields from normalized object ------------------------------
INPUT_TOKENS="unavailable"
OUTPUT_TOKENS="unavailable"
CACHE_READ_TOKENS="unavailable"
CACHE_CREATION_TOKENS="unavailable"
TOTAL_TOKENS="unavailable"
ESTIMATED_COST="unavailable"

if [ "$_CCUSAGE_NORMALIZED" != "null" ] && command -v jq >/dev/null 2>&1; then
  INPUT_TOKENS=$(printf '%s\n' "$_CCUSAGE_NORMALIZED" | jq -r '.inputTokens // "unavailable"' 2>/dev/null || echo "unavailable")
  OUTPUT_TOKENS=$(printf '%s\n' "$_CCUSAGE_NORMALIZED" | jq -r '.outputTokens // "unavailable"' 2>/dev/null || echo "unavailable")
  CACHE_READ_TOKENS=$(printf '%s\n' "$_CCUSAGE_NORMALIZED" | jq -r '.cacheReadTokens // "unavailable"' 2>/dev/null || echo "unavailable")
  CACHE_CREATION_TOKENS=$(printf '%s\n' "$_CCUSAGE_NORMALIZED" | jq -r '.cacheCreationTokens // "unavailable"' 2>/dev/null || echo "unavailable")
  TOTAL_TOKENS=$(printf '%s\n' "$_CCUSAGE_NORMALIZED" | jq -r '.totalTokens // "unavailable"' 2>/dev/null || echo "unavailable")
  ESTIMATED_COST=$(printf '%s\n' "$_CCUSAGE_NORMALIZED" | jq -r 'if .totalCost != null and .totalCost > 0 then "$\(.totalCost)" else "unavailable" end' 2>/dev/null || echo "unavailable")
fi

# -- Sanitized 26-column ledger row -------------------------------------------
LEDGER_ROW="| $DATE_ONLY | $OPT_PR | $OPT_PROMPT_ID | $OPT_PHASE | $OPT_LINKED_PR | $OPT_REPO_AREA | $OPT_SESSION_URL | $OPT_MODEL | $OPT_CHAT_STRATEGY | $CCUSAGE_SOURCE | $INPUT_TOKENS | $OUTPUT_TOKENS | $CACHE_READ_TOKENS | $CACHE_CREATION_TOKENS | $TOTAL_TOKENS | $ESTIMATED_COST | $DELTA_INPUT | $DELTA_OUTPUT | $DELTA_CACHE_READ | $DELTA_CACHE_CREATION | $DELTA_TOTAL | $DELTA_COST | $OPT_WASTE_CLASSIFICATION | $OPT_MAIN_DRIVERS | $OPT_FOLLOW_UP_PATCHES | $OPT_EFFICIENCY_LESSON |"

# -- Print usage note ---------------------------------------------------------
printf '\n=== AI Usage Note ===\n'
printf 'Timestamp:    %s\n' "$TIMESTAMP"
printf 'Repo:         %s\n' "$REPO"
printf 'Branch:       %s\n' "$BRANCH"
printf 'Diff stats:   %s\n' "$DIFF_STATS"
printf 'Usage source: %s\n' "$CCUSAGE_SOURCE"
printf 'Prompt ID:    %s\n' "$OPT_PROMPT_ID"
printf 'Phase:        %s\n' "$OPT_PHASE"

if [ "$_CCUSAGE_NORMALIZED" != "null" ] && command -v jq >/dev/null 2>&1; then
  printf 'Session usage (normalized from ccusage):\n'
  printf '  input=%s  output=%s  cache_read=%s  cache_creation=%s  total=%s  cost=%s\n' \
    "$INPUT_TOKENS" "$OUTPUT_TOKENS" "$CACHE_READ_TOKENS" "$CACHE_CREATION_TOKENS" "$TOTAL_TOKENS" "$ESTIMATED_COST"
elif [ -n "$CCUSAGE_JSON" ]; then
  printf 'Usage data:   ccusage returned data (install jq for parsed view)\n'
else
  printf 'Usage data:   not available\n'
  printf '              Fallbacks:\n'
  printf '              1. npx ccusage@latest session  (install Node if needed)\n'
  printf '              2. Claude Code status line token count\n'
  printf '              3. Manual estimate from task scope\n'
fi

if [ -n "$OPT_DELTA_FROM_BASELINE" ]; then
  printf 'Delta:        in=%s  out=%s  cr=%s  cc=%s  total=%s  cost=%s\n' \
    "$DELTA_INPUT" "$DELTA_OUTPUT" "$DELTA_CACHE_READ" "$DELTA_CACHE_CREATION" "$DELTA_TOTAL" "$DELTA_COST"
fi

if "$SNAPSHOT_WRITTEN"; then
  printf 'Snapshot:     %s\n' "$SNAPSHOT_FILE"
else
  printf 'Snapshot:     skipped (jq and python3 unavailable)\n'
fi
printf '=== End Usage Note ===\n\n'
printf 'Paste this line into the PR body (fill bracketed fields):\n'
printf '**Usage note:** Low/Medium/High; source: %s; main drivers: [fill]; justified: yes/partially/no; next efficiency improvement: [fill]\n\n' "$CCUSAGE_SOURCE"

# -- Print sanitized ledger row -----------------------------------------------
printf '=== Sanitized Ledger Row (for docs/ai/USAGE_LEDGER.md) ===\n'
printf '%s\n' "$LEDGER_ROW"
printf '=== End Ledger Row ===\n\n'

# -- Append ledger row if requested -------------------------------------------
if "$OPT_APPEND_LEDGER"; then
  if [ ! -f "$LEDGER_FILE" ]; then
    printf 'WARNING: %s not found — ledger row not appended.\n' "$LEDGER_FILE" >&2
    printf 'Paste the row above manually into docs/ai/USAGE_LEDGER.md.\n' >&2
  else
    printf '%s\n' "$LEDGER_ROW" >> "$LEDGER_FILE"
    printf 'Ledger row appended to %s\n' "$LEDGER_FILE"
  fi
fi
