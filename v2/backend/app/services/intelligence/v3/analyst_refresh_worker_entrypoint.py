"""Runnable entrypoint for the Stage 3.2 analyst refresh worker.

This is the separately-runnable process that consumes durable
``analyst_refresh_jobs`` rows OUTSIDE the synchronous Run Intel v3 HTTP request.

── Manual validation (one pass) ──────────────────────────────────────────────
    cd v2/backend
    python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint

── Continuous loop (local) ───────────────────────────────────────────────────
    python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint \
        --loop --interval-seconds 900

── Railway ───────────────────────────────────────────────────────────────────
Run this as a SEPARATE Railway service (NOT the web service). The web service
keeps its existing ``railway.toml`` start command (uvicorn) untouched. For the
worker service set the start command to:

    python -m app.services.intelligence.v3.analyst_refresh_worker_entrypoint --loop

It reuses the same repo, the same env vars (Supabase + provider/LLM keys), and
the same Supabase service-role client as the web service. ``INTEL_V3_ANALYST_
REFRESH_WORKER_INTERVAL_SECONDS`` overrides the loop interval (default 900s).

The worker is safe to run before migration 018 is applied — it will simply find
zero due jobs and exit cleanly.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logger = logging.getLogger("intel_v3.analyst_refresh_worker_entrypoint")


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
        "intel_v3.analyst_refresh_worker_entrypoint mode=loop interval_seconds=%s",
        interval_seconds,
    )
    while True:
        try:
            result = await worker.run_once()
            logger.info(
                "intel_v3.analyst_refresh_worker_entrypoint mode=loop result=%s",
                result.to_dict(),
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
        default=float(
            os.getenv("INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS", "900")
        ),
        help="Loop interval in seconds (only used with --loop; default 900).",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(loop=args.loop, interval_seconds=args.interval_seconds))


if __name__ == "__main__":
    sys.exit(main())
