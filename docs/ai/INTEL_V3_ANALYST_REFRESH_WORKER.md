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

## Supabase SQL — required, manual

Apply `v2/database/018_analyst_refresh_jobs.sql` once via the Supabase SQL
Editor (production project, maintenance window). It is additive and re-runnable.
Until it is applied, the seam still logs + returns `refresh_requested` and the
worker finds zero due jobs — nothing crashes.

Verify after applying:

```sql
SELECT * FROM public.analyst_refresh_jobs LIMIT 1;   -- 0 rows is fine
```

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
python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint \
    --loop --interval-seconds 900
```

### Railway

Run the worker as a **separate Railway service** in the same repo — do **not**
change the existing web service. The web service keeps its current
`v2/backend/railway.toml` start command (uvicorn) untouched.

For the new worker service set the start command to:

```
python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint --loop
```

`v2/backend/Procfile` also carries a `worker:` process line documenting the same
command. The worker reuses the same env vars as the web service (Supabase
service-role key + provider/LLM keys) and the same Supabase client. The loop
interval can be overridden with `INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS`
(default 900s).

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
