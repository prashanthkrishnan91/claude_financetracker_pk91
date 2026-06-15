"""Runnable entrypoint for the Stage 3.2 analyst refresh worker.

This is the separately-runnable process that consumes durable
``analyst_refresh_jobs`` rows OUTSIDE the synchronous Run Intel v3 HTTP request.

The worker is **polling, not event-driven**: clicking Run Intel v3 enqueues /
touches durable jobs but does NOT wake the worker — the worker picks them up on
its next poll. The loop interval is therefore the validation-visibility knob.

── Manual validation (one pass) ──────────────────────────────────────────────
    cd v2/backend
    python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint

── Continuous loop (local) ───────────────────────────────────────────────────
    python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint --loop

── Railway ───────────────────────────────────────────────────────────────────
Run this as a SEPARATE Railway service (NOT the web service). The web service
keeps its existing start command (uvicorn) untouched. For the worker service
set the start command to:

    python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint --loop

It reuses the same repo, the same env vars (Supabase + provider/LLM keys), and
the same Supabase service-role client as the web service.

``INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS`` overrides the loop interval
(default 60s). During production validation, keep it at 60 so an enqueue from
Run Intel v3 is picked up within ~a minute; raise it for steady-state once
behaviour is confirmed. Invalid / missing / non-positive values fall back to the
60s default. ``--interval-seconds`` on the CLI overrides the env var.

The worker is safe to run before migration 018 is applied — it will simply find
zero due jobs and exit / poll cleanly.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("intel_v3.analyst_refresh_worker_entrypoint")

# Env var + safe default for the loop poll interval. 60s keeps production
# validation legible: a Run Intel v3 enqueue is consumed within ~a minute
# rather than after the previous 15-minute default.
_INTERVAL_ENV = "INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS"
_MASTER_ENABLED_ENV = "INTEL_BACKGROUND_WORKERS_ENABLED"
_WORKER_ENABLED_ENV = "INTEL_V3_RESEARCH_WORKERS_ENABLED"
_ALLOW_AGGRESSIVE_ENV = "COST_GUARD_ALLOW_AGGRESSIVE_POLLING"
DEFAULT_INTERVAL_SECONDS = 60.0
# Cost guard: minimum safe polling interval for this LLM-calling worker.
# Clamped unless COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true.
MIN_INTERVAL_SECONDS = 43200.0  # 12 hours


def _is_master_enabled() -> bool:
    raw = (os.getenv(_MASTER_ENABLED_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _is_worker_enabled() -> bool:
    raw = (os.getenv(_WORKER_ENABLED_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")

# Drain loop guardrails — when jobs remain after a batch the worker may
# continue draining immediately without sleeping for the full poll interval.
# These caps prevent runaway LLM calls while eliminating artificial 60-second
# gaps between immediately-due batches.
MAX_DRAIN_BATCHES_PER_CYCLE = 8   # 8 × 10 tickers = 80 max per cycle (>34 portfolio)
MAX_DRAIN_RUNTIME_SECONDS_PER_CYCLE = 300.0  # 5-minute wall-clock cap per drain cycle


def _resolve_interval_seconds() -> float:
    """Resolve the loop poll interval from the env var, with a safe fallback.

    Missing, non-numeric, or non-positive values fall back to
    ``DEFAULT_INTERVAL_SECONDS`` so a fat-fingered env var can never make the
    worker sleep forever or busy-loop.
    """
    raw = (os.getenv(_INTERVAL_ENV) or "").strip()
    if not raw:
        configured = DEFAULT_INTERVAL_SECONDS
    else:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "intel_v3.analyst_refresh_worker_entrypoint invalid %s=%r — "
                "using default %ss",
                _INTERVAL_ENV, raw, DEFAULT_INTERVAL_SECONDS,
            )
            configured = DEFAULT_INTERVAL_SECONDS
        else:
            if val <= 0:
                logger.warning(
                    "intel_v3.analyst_refresh_worker_entrypoint non-positive %s=%r — "
                    "using default %ss",
                    _INTERVAL_ENV, raw, DEFAULT_INTERVAL_SECONDS,
                )
                configured = DEFAULT_INTERVAL_SECONDS
            else:
                configured = val

    allow_aggressive = (os.getenv(_ALLOW_AGGRESSIVE_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on"
    )
    if not allow_aggressive and configured < MIN_INTERVAL_SECONDS:
        logger.warning(
            "COST_GUARD intel_v3.analyst_refresh_worker_entrypoint interval_clamped "
            "requested=%ss min=%ss effective=%ss "
            "set %s=true to allow shorter intervals",
            configured, MIN_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS, _ALLOW_AGGRESSIVE_ENV,
        )
        configured = MIN_INTERVAL_SECONDS
    logger.info(
        "COST_GUARD intel_v3.analyst_refresh_worker_entrypoint effective_interval_seconds=%s",
        configured,
    )
    return configured


async def _drain_cycle(
    worker: Any,
    *,
    max_batches: int,
    max_runtime_seconds: float,
    now: "datetime | None" = None,
) -> "tuple[list, int, bool]":
    """Run multiple worker batches in one cycle without sleeping between them.

    Returns (results_list, total_duration_ms, idle_delay_skipped).
    idle_delay_skipped=True means at least one inter-batch sleep was skipped
    because jobs remained and budget allowed immediate continuation.

    Guardrails:
      - max_batches: hard cap on LLM batch invocations per cycle.
      - max_runtime_seconds: wall-clock cap; stops draining if exceeded.
    Certification/prewarm is handled inside worker.run_once() — this function
    does not weaken that contract.
    """
    results = []
    idle_delay_skipped = False
    cycle_start = time.monotonic()

    for _ in range(max_batches):
        result = await worker.run_once(now=now)
        results.append(result)

        elapsed = time.monotonic() - cycle_start
        if not result.run_resumable:
            break
        if result.claimed_job_count == 0:
            # run_resumable=True but no jobs were claimed — all remaining jobs are
            # in retry backoff and not yet due. Stop draining; the next poll cycle
            # will pick them up when their backoff expires. Do NOT set
            # idle_delay_skipped — we did not make meaningful progress this batch.
            logger.info(
                "intel_v3.analyst_refresh_worker_drain_cycle_stopped "
                "reason=backoff_or_no_due_jobs run_resumable=%s batches_so_far=%d",
                result.run_resumable, len(results),
            )
            break
        if elapsed >= max_runtime_seconds:
            logger.info(
                "intel_v3.analyst_refresh_worker_drain_cycle_runtime_cap_reached "
                "elapsed_seconds=%.1f max_runtime_seconds=%s batches_so_far=%d",
                elapsed, max_runtime_seconds, len(results),
            )
            break
        # Jobs remain and budget allows — skip the poll-interval sleep
        idle_delay_skipped = True

    duration_ms = int((time.monotonic() - cycle_start) * 1000)
    return results, duration_ms, idle_delay_skipped


async def _run(*, loop: bool, interval_seconds: float) -> int:
    # Imported lazily so importing this module (e.g. in tests) does not require
    # a configured Supabase client.
    from ....database import get_supabase_client
    from .analyst_refresh_worker_v1 import AnalystRefreshWorker

    client = get_supabase_client()
    worker = AnalystRefreshWorker(client=client)

    if not loop:
        result = await worker.run_once()
        logger.info(
            "intel_v3.analyst_refresh_worker_entrypoint mode=single_run result=%s",
            result.to_dict(),
        )
        return 0

    logger.info(
        "intel_v3.analyst_refresh_worker_entrypoint mode=loop interval_seconds=%s "
        "max_drain_batches_per_cycle=%d max_drain_runtime_seconds=%s "
        "polling=true note=run_intel_enqueues_jobs_but_does_not_wake_worker",
        interval_seconds,
        MAX_DRAIN_BATCHES_PER_CYCLE,
        MAX_DRAIN_RUNTIME_SECONDS_PER_CYCLE,
    )
    while True:
        try:
            drain_results, drain_duration_ms, idle_delay_skipped = await _drain_cycle(
                worker,
                max_batches=MAX_DRAIN_BATCHES_PER_CYCLE,
                max_runtime_seconds=MAX_DRAIN_RUNTIME_SECONDS_PER_CYCLE,
            )
            batches_drained = len(drain_results)
            last = drain_results[-1] if drain_results else None
            total_succeeded = sum(len(r.succeeded_tickers) for r in drain_results)
            total_failed = sum(len(r.failed_tickers) for r in drain_results)
            total_attempted_llm = sum(r.attempted_llm_calls for r in drain_results)
            total_successful_llm = sum(r.successful_llm_calls for r in drain_results)
            total_failed_llm = sum(r.failed_llm_calls for r in drain_results)
            run_resumable_after_cycle = last.run_resumable if last else False

            next_poll_at = (
                datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)
            ).isoformat()
            # time_to_worker_certified_snapshot_ms: when run_resumable_after_cycle
            # is False, all batches drained and prewarm was attempted this cycle.
            # The drain_duration_ms covers worker cycle time (excludes initial
            # poll-interval sleep before the worker woke).
            time_to_certified_ms = drain_duration_ms if not run_resumable_after_cycle else -1
            logger.info(
                "intel_v3.analyst_refresh_worker_drain_cycle_summary "
                "worker_batches_drained=%d worker_drain_total_duration_ms=%d "
                "worker_idle_delay_skipped=%s run_resumable_after_cycle=%s "
                "total_succeeded=%d total_failed=%d "
                "attempted_llm_calls=%d successful_llm_calls=%d failed_llm_calls=%d "
                "time_to_worker_certified_snapshot_ms=%d "
                "next_poll_at=%s interval_seconds=%s",
                batches_drained,
                drain_duration_ms,
                idle_delay_skipped,
                run_resumable_after_cycle,
                total_succeeded,
                total_failed,
                total_attempted_llm,
                total_successful_llm,
                total_failed_llm,
                time_to_certified_ms,
                next_poll_at,
                interval_seconds,
            )
        except Exception as exc:  # never let one bad pass kill the loop
            logger.exception(
                "intel_v3.analyst_refresh_worker_entrypoint loop pass failed: %s", exc
            )
        await asyncio.sleep(interval_seconds)


def main(argv: "list[str] | None" = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Intel v3 Stage 3.2 — durable analyst refresh worker",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously instead of a single pass.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help=(
            "Loop interval in seconds (only used with --loop). Overrides "
            f"{_INTERVAL_ENV}; when neither is set the default is "
            f"{DEFAULT_INTERVAL_SECONDS:g}s."
        ),
    )
    args = parser.parse_args(argv)

    if not _is_master_enabled():
        logger.info(
            "COST_GUARD intel_v3.analyst_refresh_worker_entrypoint master_disabled — "
            "set %s=true to allow background workers. Exiting cleanly.",
            _MASTER_ENABLED_ENV,
        )
        return 0

    if not _is_worker_enabled():
        logger.info(
            "COST_GUARD intel_v3.analyst_refresh_worker_entrypoint worker_disabled — "
            "set %s=true to enable this worker. Exiting cleanly.",
            _WORKER_ENABLED_ENV,
        )
        return 0

    interval_seconds = (
        args.interval_seconds
        if args.interval_seconds is not None
        else _resolve_interval_seconds()
    )
    return asyncio.run(_run(loop=args.loop, interval_seconds=interval_seconds))


if __name__ == "__main__":
    sys.exit(main())
