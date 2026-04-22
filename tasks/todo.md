# Portfolio Engine v2 — Execution DAG + Cache + IO Layer + Single-LLM Guarantee

## Goal
Refactor the single-LLM pipeline into a strict DAG:
  REQUEST → CACHE → CONTEXT BUILDER (pure) → PARALLEL IO → SINGLE LLM → PERSIST → RESPONSE

## Current state (verified)
- `services/ai/context_builder.py` — builder exists, but not pure (does DB reads for positions/insights/macro).
- `services/agents/orchestrator.py` — single-LLM call behind `LLM_SEMAPHORE`, good foundation.
- `services/agents/llm.py` — Sonnet primary + Haiku fallback + HTTP-level backoff. HTTP retries/failover stay (not a pipeline re-entry).
- `services/agents/data_sources.py` — async fetchers with try/except, **no cache**, **no dedup**.
- `services/recommendation_engine.queue_agent_run` — already has single-run lock + 2-min cache + 10-min stale recovery.
- `routers/recommendations.py` — `/refresh` returns `status="queued"|"reused"` — Task 6 wants `"in_progress"`.

## Plan

1. **Cache layer** — new `services/cache/market_cache.py`: TTL in-memory cache + per-key `asyncio.Lock` dedup + `get_or_fetch(key, ttl, factory)` API. Keys: `price:{sym}`, `news:{sym}`, `macro:snapshot`.
2. **Parallel IO layer** — new `services/ai/io_layer.py`: `fetch_market_bundle(tickers, ...)` using `asyncio.gather`, cache-first, HTTP-layer retries, never raises, never LLM.
3. **Pure context builder** — `build_portfolio_context` becomes pure (inputs: positions, insights, market_data, macro_summary); add thin `build_portfolio_context_from_db(...)` wrapper for legacy callers.
4. **Orchestrator** — resolve positions/insights/macro once (DB), call io_layer for parallel fetch (cached), feed pure context builder, single LLM call (semaphore + one-call-per-run assertion).
5. **Stage timings** — `time.perf_counter()` per stage + total elapsed log.
6. **Router dedup shape** — `/refresh` returns `{status: "in_progress", job_id}` when reusing, `"queued"` when new.
7. **Failure isolation** — io_layer wraps each fetch in try/except → cache fallback → neutral value.
8. **Tests** — cache TTL+dedup, io_layer cache-first+failure, context_builder purity, orchestrator single-LLM count.

## Non-goals (explicit)
- No financial logic changes.
- No output schema changes (agent_insights, recommendations, AgentRunStatus stay stable).
- Do NOT touch `v1/*` or any router outside `recommendations.py`.

## Success criteria
- `pytest v2/backend/tests -q` passes
- Orchestrator invokes `LLMClient.ask_json` exactly once per run
- `io_layer` hits cache on second call for same ticker within TTL
- `/refresh` returns `in_progress` for concurrent requests
