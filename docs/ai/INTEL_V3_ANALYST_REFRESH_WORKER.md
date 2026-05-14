# Intel v3 Stage 3.2 — Continuous Intelligence Plane v1 (analyst refresh worker)

Runtime + entrypoint reference for the durable owned-position analyst evidence
refresh worker. This is the background plane that consumes the refresh requests
the Stage 3.1 seam records, so stale analyst evidence is refreshed **outside**
the synchronous Run Intel v3 HTTP request.

## How the pieces fit

```
Run Intel v3 (HTTP, fast certification path)
  └─ EvidenceRefreshOrchestrator
       └─ AnalystRefreshRequestSeam        ← Stage 3.1 (logs) + Stage 3.2 (enqueues)
            └─ analyst_refresh_jobs table  ← durable queue (migration 018)

analyst_refresh_worker_v1  (separate process — Railway worker service / manual)
  └─ claim_due_jobs()                      ← claims pending/failed due jobs
  └─ FullPortfolioAnalystRefreshAdapter    ← drives existing AgentOrchestrator
       └─ recommendations / agent_insights ← persisted via the existing path
  └─ mark_job_succeeded / mark_job_failed  ← per-ticker outcome, retry backoff
```

- The synchronous request never performs analyst/LLM work — the seam only does
  a fast idempotent queue upsert.
- The deterministic Intel v3 policy still owns visible Buy/Hold/Trim/Sell. The
  worker refreshes **evidence only** and never imports the decision policy.
- Failed tickers stay `failed` with an exponential-backoff retry — no fabricated
  freshness.
- **The worker is polling, not event-driven.** Clicking Run Intel v3
  enqueues / touches durable jobs but does NOT wake the worker — the worker
  picks them up on its next poll. The loop interval (`INTEL_V3_ANALYST_REFRESH_
  WORKER_INTERVAL_SECONDS`, default **60s**) is therefore the production-
  validation visibility knob.

## Supabase SQL — required, manual

Apply `v2/database/018_analyst_refresh_jobs.sql` once via the Supabase SQL
Editor (production project, maintenance window). It is additive and re-runnable.
Until it is applied, the seam still logs + returns `refresh_requested` and the
worker finds zero due jobs — nothing crashes.

Verify after applying:

```sql
SELECT * FROM public.analyst_refresh_jobs LIMIT 1;   -- 0 rows is fine
```

## Idempotency & window semantics (Stage 3.2 v1)

The idempotency key is `(user_id, ticker, refresh_window)` where `refresh_window`
is a per-UTC-day bucket. The enqueue path (`enqueue_refresh_jobs`) is idempotent
and always leaves **exactly one row per key** — never a duplicate.

`enqueue_refresh_jobs` is only ever called from the Stage 3.1 seam on an
**explicit user-triggered Run Intel v3** (the worker's own automatic retries go
through `mark_job_failed` → exponential backoff, never through enqueue). So an
existing row's worker-backoff timer must not block a refresh the user
explicitly asked for *now*. Per-state behaviour for an existing same-window row:

| Existing row state | Re-click behaviour | Why |
|---|---|---|
| `pending` | touched (`requested_at` bumped) | already claimable — nothing to do |
| `claimed`, claim in-flight (`claimed_at` within `STALE_CLAIM_TIMEOUT_SECONDS`, 600s) | touched only | a worker is mid-processing — must not be stolen |
| `claimed`, claim stale (older than 600s) | **reopened** → `pending`, attempts reset | the claiming worker crashed/hung — recover the abandoned job |
| `failed`, attempts remaining | **made due now** → `status=pending`, `next_retry_at=now`, **attempts preserved** | the user explicitly asked for a refresh now; the worker-backoff timer governs *automatic* retries only and must not make an explicit request wait |
| `failed`, attempts exhausted | **reopened** → `pending`, attempts reset | an exhausted job must not permanently suppress a later legitimate retry while the evidence is still stale |
| `succeeded` | **reopened** → `pending`, attempts reset | the seam only re-enqueues tickers still classified stale/HARD_STALE, so a same-window re-request means the prior refresh did not clear the staleness |

Every branch is a single in-place `UPDATE` (or one `INSERT` for a brand-new
ticker) — still exactly one row per key, and no analyst/LLM work runs in the
request. The key distinction: **explicit user refresh = make claimable now;
worker-internal retry = honour the exponential backoff.** Diagnostics for every
enqueue are logged on `intel_v3.analyst_refresh_job_enqueued`
(`created` / `touched` / `made_due` / `reopened` / `reopened_failed` /
`failed_not_due_before` / `statuses_before` / `statuses_after` /
`next_retry_min` / `next_retry_max`).

## Running the worker

The entrypoint is `app.services.intelligence.v3.analyst_refresh_worker_entrypoint`.

### Manual validation (one pass)

```bash
cd v2/backend
python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint
```

Logs one `intel_v3.analyst_refresh_worker_run_summary` line and exits 0.

### Continuous loop (local)

```bash
cd v2/backend
# Polls every INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS (default 60s).
python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint --loop
# Or override the interval explicitly (CLI flag wins over the env var):
python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint \
    --loop --interval-seconds 60
```

Each loop pass logs an `intel_v3.analyst_refresh_worker_loop_summary` line with
`mode=loop interval_seconds=… next_poll_at=… claimed_job_count=… selected_ticker_count=…
succeeded_count=… failed_count=…` so a poll that found `claimed_job_count=0` is
visibly distinct from one that drained jobs.

### Railway

Run the worker as a **separate Railway service** in the same repo — do **not**
change the existing web service. Both services use the single shared
`v2/backend/railway.toml` file:

- **Main web service**: leave `PROCESS_TYPE` unset (empty string or not set). The
  start command conditionally runs uvicorn.
- **Worker service**: set `PROCESS_TYPE=worker` as an environment variable. The
  start command conditionally runs
  `python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint --loop`.

The shared `railway.toml` uses a shell conditional (`if PROCESS_TYPE=worker...`)
to branch at startup. Both services set `root = "v2/backend"` and share the same
Supabase service-role key + provider/LLM env vars. The loop interval is
controlled by `INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS` (**default
60s**); invalid / missing / non-positive values fall back to the 60s default.

**Production validation:** the worker is polling, not event-driven — a Run
Intel v3 click enqueues / touches jobs but does **not** wake the worker. During
validation, set `INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS=60` on the
worker service so an enqueue is consumed within ~a minute and the
`analyst_refresh_worker_loop_summary` log moves promptly from
`claimed_job_count=0` to a non-zero drain. Raise the interval for steady-state
once behaviour is confirmed.

`v2/backend/Procfile` also carries a `worker:` process line documenting the same
command for local development.

The worker is disabled cleanly by `INTEL_V3_ANALYST_REFRESH_ENABLED=0` on the
seam side (no jobs get enqueued); the worker process itself simply finds an
empty queue.

## Observability — Railway log keys

| Log key | Emitted by | Proves |
|---|---|---|
| `intel_v3.analyst_refresh_requested` | seam | request identified stale analyst evidence, zero in-request LLM calls |
| `intel_v3.analyst_refresh_job_enqueued` | job store | durable job(s) created/touched (idempotent) |
| `intel_v3.analyst_refresh_job_claimed` | job store | worker claimed a due job |
| `intel_v3.analyst_refresh_worker_tickers_selected` | worker | owned tickers selected + prioritised |
| `intel_v3.analyst_refresh_worker_ticker_succeeded` | worker | a ticker's evidence was refreshed + persisted |
| `intel_v3.analyst_refresh_worker_ticker_failed` | worker | a ticker stayed stale + its next retry time |
| `intel_v3.analyst_refresh_worker_run_summary` | worker | claimed / selected / succeeded / failed / LLM-call counts / duration |
| `intel_v3.analyst_refresh_worker_loop_summary` | entrypoint | per-poll: `interval_seconds` / `next_poll_at` / claimed / selected / succeeded / failed — distinguishes an idle poll from a drain |

### Production validation gate

Production moved from `refresh_requested` to fresh persisted evidence when, for
the stale inventory:

1. Run Intel v3 logs `intel_v3.analyst_refresh_job_enqueued ... created=N` with
   `attempted_llm_calls=0` still in `intel_v3_freshness_summary`.
2. A worker pass logs `intel_v3.analyst_refresh_worker_run_summary ... claimed=N
   succeeded=M` with `M > 0` and `persisted_ticker_success_count=M`.
3. The succeeded tickers' `recommendations.created_at` / `agent_insights.created_at`
   move to near-current in actual DB rows.
4. A subsequent Run Intel v3 reads the now-fresh evidence and the run mode
   improves (analyst sources no longer STALE/HARD_STALE for refreshed tickers).
5. Any genuine failure stays `failed` in `analyst_refresh_jobs` with a real
   `next_retry_at` — never silently marked succeeded.
