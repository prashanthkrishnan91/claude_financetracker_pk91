# Railway Cost Guard

**Created:** 2026-06-15 — Emergency response to Supabase storage overrun + Railway RAM cost incident.

---

## Incident Summary

**What happened:**
- Supabase DB exceeded 0.5 GB free tier storage.
  - `public.intel_v3_snapshots`: ~497.9 MB (full JSON payloads written every 60s Watchtower cycle).
  - `public.market_snapshots`: ~18.1 MB.
  - `agent_runs`, `agent_insights`, `agent_features`, `recommendations`, `decision_log` also accumulated unbounded history.
- Railway monthly cost: ~$18 halfway through the billing cycle with the app idle.
  - Watchtower worker: always-on, polling every 60s, triggering writes on each cycle.
  - Analyst/research worker: always-on, triggering LLM calls.
  - Email delivery worker: always-on.

**Root cause:** Three background workers ran continuously with no RAM/cost gate and unboundedly short polling intervals. The snapshot idempotency check (source_hash comparison) reduced some writes but not enough — watchtower was still spinning RAM and doing DB reads every 60s.

---

## Emergency Env Vars (already set in Railway)

These must stay set until you explicitly choose to re-enable workers:

| Env Var | Value | Effect |
|---|---|---|
| `INTEL_BACKGROUND_WORKERS_ENABLED` | `false` | Master kill switch — all workers exit 0 immediately |
| `INTEL_V3_WATCHTOWER_ENABLED` | `false` | Per-worker flag for watchtower |
| `INTEL_V3_RESEARCH_WORKERS_ENABLED` | `false` | Per-worker flag for analyst + research lanes |
| `ALERT_EMAIL_DELIVERY_ENABLED` | `false` | Per-worker flag for email delivery |
| `INTEL_V3_SNAPSHOT_WRITES_ENABLED` | `false` | Prevents writes to intel_v3_snapshots |

**Do NOT re-enable workers without reading "Re-activation checklist" below.**

---

## What Changed (this PR)

### 1. Master kill switch in all worker entrypoints

All three worker entrypoints now check `INTEL_BACKGROUND_WORKERS_ENABLED` as a master gate before checking their own flag. If false, the worker logs one `COST_GUARD` message and exits 0 — no DB connection, no polling loop, no RAM held.

Files changed:
- `v2/backend/app/services/intelligence/v3/watchtower_worker_entrypoint.py`
- `v2/backend/app/services/intelligence/v3/analyst_refresh_worker_entrypoint.py`
- `v2/backend/app/services/alert/alert_email_delivery_worker_entrypoint.py`

Railway start commands are **unchanged** — the fix is entirely in application code and env flags.

### 2. Polling interval clamping

Each worker now enforces a safe minimum interval regardless of configured values:

| Worker | Minimum | Env override to bypass |
|---|---|---|
| Watchtower | 6 hours (21,600s) | `COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true` |
| Analyst/research | 12 hours (43,200s) | `COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true` |
| Email delivery | 24 hours (86,400s) | `COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true` |

Leave `COST_GUARD_ALLOW_AGGRESSIVE_POLLING=false` (default) in production.

### 3. Snapshot write guard

`intel_v3_service._persist_snapshot()` now checks `intel_v3_snapshot_writes_enabled` before writing. When false (default), Intel v3 runs still compute results and return them in the HTTP response but do not persist to `intel_v3_snapshots`. Read paths (GET snapshot) are unaffected.

### 4. Config defaults

All three new flags default to `false` in `app/config.py`:
- `intel_background_workers_enabled: bool = False`
- `intel_v3_snapshot_writes_enabled: bool = False`
- `cost_guard_allow_aggressive_polling: bool = False`

### 5. Retention SQL

`v2/database/cost_guard_retention_cleanup.sql` — bounded DELETE policies for all generated tables. Run manually on a schedule. No `TRUNCATE CASCADE`. Does not touch core user data.

---

## Railway Validation Steps

After any env change or redeploy:

1. **Redeploy all Railway services** (or restart workers) after setting env vars.
2. **Confirm COST_GUARD exit in logs:**
   - Filter Railway logs for `COST_GUARD`.
   - Each worker should show exactly one log line like:
     ```
     COST_GUARD intel_v3.watchtower_worker_entrypoint master_disabled — set INTEL_BACKGROUND_WORKERS_ENABLED=true to allow background workers. Exiting cleanly.
     ```
   - Worker process should then exit (no further log output from that process).
3. **Confirm RAM/CPU drops:**
   - After 10–15 minutes, worker service RAM should be near zero (process exited).
   - If RAM stays elevated, check Railway logs — the process may be respawning due to `restart_policy=on_failure`. A clean exit (code 0) should not trigger restart.
4. **Confirm no new snapshot rows:**
   - Run in Supabase SQL editor:
     ```sql
     SELECT COUNT(*), MAX(created_at) FROM public.intel_v3_snapshots;
     ```
   - The `MAX(created_at)` should not advance while workers are disabled.

---

## Supabase Validation Steps

**Check generated table sizes:**
```sql
SELECT
  relname AS table_name,
  pg_size_pretty(pg_total_relation_size('public.' || relname)) AS total_size,
  n_live_tup AS live_rows
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size('public.' || relname) DESC
LIMIT 20;
```

**Run retention cleanup** (after confirming no worker is writing):
```sql
-- Review v2/database/cost_guard_retention_cleanup.sql first.
-- Then run it in Supabase SQL editor.
-- Post-cleanup, run VACUUM ANALYZE on pruned tables:
VACUUM ANALYZE public.intel_v3_snapshots;
VACUUM ANALYZE public.market_snapshots;
VACUUM ANALYZE public.research_artifacts;
```

**Confirm tables stay bounded:**
- Run the size query again after cleanup.
- With workers disabled and snapshot writes off, sizes should not grow.

---

## Cost-Guard Health Diagnostic

```bash
# From repo root — reports all flag states and effective intervals:
python scripts/cost_guard_health.py
```

Expected output when all guards are active:
```
RESULT: SAFE — all cost guards active
```

---

## Re-activation Checklist

**Before re-enabling any worker:**

1. Confirm Supabase DB is below 0.3 GB (leave headroom before 0.5 GB limit).
2. Confirm retention cleanup SQL has been run and verified.
3. Set `INTEL_V3_SNAPSHOT_WRITES_ENABLED=true` ONLY if you accept snapshot storage growth.
4. Set `INTEL_BACKGROUND_WORKERS_ENABLED=true` AND the specific worker flag.
5. Set worker interval env vars to safe values (at or above the minimums):
   - `INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS=21600` (6h minimum)
   - `INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS=43200` (12h minimum)
   - `ALERT_EMAIL_DELIVERY_WORKER_INTERVAL_SECONDS=86400` (24h minimum)
6. Deploy, then watch Railway logs for `COST_GUARD effective_interval_seconds=` — confirm interval is correct.
7. Monitor Supabase DB size over 24h. If growth exceeds ~50 MB/day, re-disable.

**Do not run Intel background workers again until this PR is merged and deployed.**

---

## Tables Protected by This Guard

| Table | Type | Guarded by |
|---|---|---|
| `intel_v3_snapshots` | Generated | `INTEL_V3_SNAPSHOT_WRITES_ENABLED=false` |
| `market_snapshots` | Generated | Workers disabled (no writer runs) |
| `agent_runs` / `agent_insights` / `agent_features` | Generated | Workers disabled |
| `recommendations` / `decision_log` | Generated | Workers disabled |
| `research_artifacts` / facts / sources | Generated | `INTEL_V3_RESEARCH_WORKERS_ENABLED=false` |
| `worker_audit_events` | Generated | Workers disabled |

**Never touched by this guard (safe):**
- `portfolios`, `positions`, `holdings`, `transactions`
- `users`, `auth`, `accounts`, `deposits`
- `plaid_items`, manually entered user data
