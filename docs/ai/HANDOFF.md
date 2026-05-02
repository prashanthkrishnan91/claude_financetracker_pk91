# AI Engineering Handoff

## Intel Reasoning v2 — PR 1 (Dormant Backend Builder)

**Status**: Merged — live on `claude/reasoning-v2-builder-Ujp9G`
**Date**: 2026-05-02
**Author**: Claude (automated)

### Scope

PR 1 implements the deterministic backend builder for Intel Reasoning v2.
It fuses analyst verdict data into a structured reasoning object and persists
it dormant inside `agent_runs.allocation["_reasoning_v2"]` per ticker.

**This PR is intentionally minimal.** Reasoning v2 is NOT exposed on any API
endpoint or InsightCard model. It must be inspected directly from the
`agent_runs` table until live-run output has been reviewed (see PR 2 guidance below).

### New module

`v2/backend/app/services/intelligence/reasoning_v2_builder.py`

- Pure deterministic function: `build_reasoning_v2(*, ticker, scorecard, analyst_verdict, provider_meta)`
- Accepts `ScoreCard` dataclass, serialised dict, or `None`
- Output schema version: `reasoning_v2.0`
- Top-level sections: `why`, `risk`, `action`, `alt_view`, `confidence`, `deploy_signals`, `evidence`, `data_quality`
- Forbidden indicator language scrubbed from all `user_text` fields
- No allocation math, dollar amounts, or position targets in output
- `deploy_signals` is metadata-only (bands, blockers, caveats — no sizing)

### Persistence

`agent_runs.allocation["_reasoning_v2"]` is a dict keyed by ticker symbol.
Each value is the full `reasoning_v2.0` structured object from the builder.

The `_reasoning_v2` key is written alongside existing per-ticker allocation
amounts in the same `agent_runs.allocation` JSONB column. No SQL migration
is required.

Wire-up site: `v2/backend/app/services/agents/orchestrator.py` — immediately
after the `allocation_map` dict comprehension in `Orchestrator.run()`.

Builder failures are caught per-ticker; a single ticker failure does not
break Run Agents or recommendation generation.

### What is NOT changed

| Area | Status |
|---|---|
| `InsightCard` model | **Not changed** — `reasoning_v2` field not added in PR 1 |
| `GET /api/v1/recommendations/` | **Not exposed** — `_reasoning_v2` stays in allocation only |
| Frontend / UI | **No change** |
| Deploy flow | **No change** |
| Score / recommendation math | **No change** |
| LLM prompts or model choices | **No change** |
| `thesis_plain_english` / Business read UI | **Remains hidden/dormant** |
| Supabase SQL migrations | **None required** |

### Current limitations (PR 1)

- `scorecard` is always `None` at the wire-up site because `thesis_engine.py` /
  `score_schema.py` do not yet exist. The `ScoreCard` stub in the builder is
  forward-compatible.
- `evidence.deterministic` is always `{}` for all tickers until a scorecard
  pipeline is implemented.
- `why.support` will be `"analyst"` for valid verdicts, `"insufficient"` otherwise.
- Agreement is `"analyst_only"` until scorecard dimensions are populated.

### How to inspect `_reasoning_v2` after a live Run Agents execution

Run the following SQL in Supabase (replace `<user_id>` and `<run_id>`):

```sql
SELECT
  id,
  status,
  finished_at,
  allocation->'_reasoning_v2' AS reasoning_v2
FROM agent_runs
WHERE user_id = '<user_id>'
  AND status = 'completed'
ORDER BY finished_at DESC
LIMIT 1;
```

To inspect a single ticker:

```sql
SELECT
  allocation->'_reasoning_v2'->'NVDA' AS nvda_reasoning_v2
FROM agent_runs
WHERE user_id = '<user_id>'
ORDER BY finished_at DESC
LIMIT 1;
```

### PR 2 guidance

PR 2 should intentionally expose / project `reasoning_v2` onto the InsightCard
or a dedicated endpoint **only after** PR 1 live-run output has been inspected
on real portfolios. Specifically:

1. Confirm `_reasoning_v2` is populated for every ticker in a completed run.
2. Confirm `why.user_text` is plain-English and free of forbidden language.
3. Confirm `deploy_signals.blockers` correctly reflects missing/conflict states.
4. Confirm no allocation math keys appear anywhere in the output.
5. Only then add `reasoning_v2` to `InsightCard` or a new endpoint.

**Do not start PR 2 until live-run inspection is complete.**
