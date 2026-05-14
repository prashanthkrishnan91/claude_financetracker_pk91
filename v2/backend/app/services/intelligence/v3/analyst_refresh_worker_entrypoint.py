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
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("intel_v3.analyst_refresh_worker_entrypoint")

# Env var + safe default for the loop poll interval. 60s keeps production
# validation legible: a Run Intel v3 enqueue is consumed within ~a minute
# rather than after the previous 15-minute default.
_INTERVAL_ENV = "INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 60.0


def _resolve_interval_seconds() -> float:
    """Resolve the loop poll interval from the env var, with a safe fallback.

    Missing, non-numeric, or non-positive values fall back to
    ``DEFAULT_INTERVAL_SECONDS`` so a fat-fingered env var can never make the
    worker sleep forever or busy-loop.
    """
    raw = (os.getenv(_INTERVAL_ENV) or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "intel_v3.analyst_refresh_worker_entrypoint invalid %s=%r — "
            "using default %ss",
            _INTERVAL_ENV, raw, DEFAULT_INTERVAL_SECONDS,
        )
        return DEFAULT_INTERVAL_SECONDS
    if val <= 0:
        logger.warning(
            "intel_v3.analyst_refresh_worker_entrypoint non-positive %s=%r — "
            "using default %ss",
            _INTERVAL_ENV, raw, DEFAULT_INTERVAL_SECONDS,
        )
        return DEFAULT_INTERVAL_SECONDS
    return val


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
        "polling=true note=run_intel_enqueues_jobs_but_does_not_wake_worker",
        interval_seconds,
    )
    while True:
        try:
            result = await worker.run_once()
            next_poll_at = (
                datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)
            ).isoformat()
            logger.info(
                "intel_v3.analyst_refresh_worker_loop_summary mode=loop "
                "interval_seconds=%s next_poll_at=%s claimed_job_count=%d "
                "selected_ticker_count=%d succeeded_count=%d failed_count=%d",
                interval_seconds,
                next_poll_at,
                result.claimed_job_count,
                len(result.selected_tickers),
                len(result.succeeded_tickers),
                len(result.failed_tickers),
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
    interval_seconds = (
        args.interval_seconds
        if args.interval_seconds is not None
        else _resolve_interval_seconds()
    )
    return asyncio.run(_run(loop=args.loop, interval_seconds=interval_seconds))


if __name__ == "__main__":
    sys.exit(main())
