"""Runnable entrypoint for the Watchtower Background Refresh Worker (Build 1D).

Keeps evidence fresh continuously without blocking click-time requests.
Price/weight evidence is refreshed inline; analyst LLM refresh is deferred
to the existing analyst_refresh_worker_v1 via enqueue_refresh_jobs.

── Manual validation (one pass) ──────────────────────────────────────────────
    cd v2/backend
    python -m app.services.intelligence.v3.watchtower_worker_entrypoint

── Continuous loop (local) ───────────────────────────────────────────────────
    python -m app.services.intelligence.v3.watchtower_worker_entrypoint --loop

── Railway ───────────────────────────────────────────────────────────────────
Run this as a SEPARATE Railway service. Set the start command to:

    python -m app.services.intelligence.v3.watchtower_worker_entrypoint --loop

It reuses the same repo, env vars, and Supabase service-role client as the
web service. Default callables are wired at startup — no manual injection needed.

``INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS`` overrides the loop interval
(default 60s). Invalid or non-positive values fall back to the default.
``--interval-seconds`` on the CLI overrides the env var.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger("intel_v3.watchtower_worker_entrypoint")

_INTERVAL_ENV = "INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 60.0


def _resolve_interval_seconds() -> float:
    raw = (os.getenv(_INTERVAL_ENV) or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "intel_v3.watchtower_worker_entrypoint invalid %s=%r — using default %ss",
            _INTERVAL_ENV, raw, DEFAULT_INTERVAL_SECONDS,
        )
        return DEFAULT_INTERVAL_SECONDS
    if val <= 0:
        logger.warning(
            "intel_v3.watchtower_worker_entrypoint non-positive %s=%r — using default %ss",
            _INTERVAL_ENV, raw, DEFAULT_INTERVAL_SECONDS,
        )
        return DEFAULT_INTERVAL_SECONDS
    return val


def _build_default_price_refresh_callable(client: Any) -> Optional[Any]:
    """Build the default PriceService callable for price evidence refresh."""
    try:
        from ....config import get_settings
        from ...price_engine import PriceService as _PriceEngine
        settings = get_settings()
    except Exception:
        return None

    async def _refresh(tickers: list[str]) -> dict[str, Any]:
        deduped: list[str] = []
        seen: set[str] = set()
        for t in tickers or []:
            key = str(t or "").upper()
            if key and key not in seen:
                seen.add(key)
                deduped.append(t)
        svc = _PriceEngine(
            finnhub_key=getattr(settings, "finnhub_api_key", "") or "",
            alpaca_key=getattr(settings, "alpaca_api_key", "") or "",
            alpaca_secret=getattr(settings, "alpaca_secret_key", "") or "",
            polygon_key=getattr(settings, "polygon_api_key", "") or "",
        )
        try:
            return await svc.fetch_prices(deduped)
        finally:
            try:
                await svc.close()
            except Exception:
                pass

    return _refresh


def _build_default_analyst_enqueue_callable(client: Any) -> Any:
    """Build the default analyst job enqueue callable via enqueue_refresh_jobs."""
    import asyncio as _asyncio

    async def _enqueue(user_id: UUID, tickers: list[str]) -> int:
        from .analyst_refresh_job_store_v1 import enqueue_refresh_jobs
        now = datetime.now(timezone.utc)
        result = await _asyncio.to_thread(
            enqueue_refresh_jobs,
            client,
            user_id=user_id,
            tickers=tickers,
            now=now,
        )
        return (
            result.created_count
            + result.touched_count
            + result.made_due_count
            + result.reopened_count
        )

    return _enqueue


async def _fetch_active_user_ids(client: Any) -> list[UUID]:
    """Fetch distinct user IDs with active position rows."""
    import asyncio as _asyncio
    try:
        result = await _asyncio.to_thread(
            lambda: client.table("positions")
            .select("user_id")
            .execute()
        )
        seen: set[str] = set()
        uids: list[UUID] = []
        for row in (result.data or []):
            uid_str = row.get("user_id") if isinstance(row, dict) else None
            if uid_str and uid_str not in seen:
                seen.add(uid_str)
                try:
                    uids.append(UUID(str(uid_str)))
                except (ValueError, TypeError):
                    pass
        return uids
    except Exception as exc:
        logger.warning("watchtower_worker_entrypoint.fetch_users_failed err=%s", exc)
        return []


async def _run_cycle_for_all_users(client: Any) -> dict[str, Any]:
    """Run one Watchtower refresh cycle for every user with active positions."""
    from .watchtower_background_refresh_worker_v1 import WatchtowerBackgroundRefreshWorker

    price_refresh = _build_default_price_refresh_callable(client)
    analyst_enqueue = _build_default_analyst_enqueue_callable(client)

    user_ids = await _fetch_active_user_ids(client)
    succeeded = 0
    failed = 0

    for user_id in user_ids:
        try:
            worker = WatchtowerBackgroundRefreshWorker(
                client=client,
                price_refresh_callable=price_refresh,
                analyst_job_enqueue_callable=analyst_enqueue,
            )
            await worker.run_refresh_cycle(user_id)
            succeeded += 1
        except Exception as exc:
            logger.warning(
                "watchtower_worker_entrypoint.cycle_failed user_id=%s err=%s",
                user_id, exc,
            )
            failed += 1

    return {
        "users_processed": succeeded + failed,
        "succeeded": succeeded,
        "failed": failed,
    }


async def _run(*, loop: bool, interval_seconds: float) -> int:
    from ....database import get_supabase_client

    client = get_supabase_client()

    if not loop:
        result = await _run_cycle_for_all_users(client)
        logger.info(
            "intel_v3.watchtower_worker_entrypoint mode=single_run "
            "users_processed=%d succeeded=%d failed=%d",
            result["users_processed"], result["succeeded"], result["failed"],
        )
        return 0

    logger.info(
        "intel_v3.watchtower_worker_entrypoint mode=loop interval_seconds=%s polling=true "
        "note=run_intel_enqueues_jobs_but_does_not_wake_worker",
        interval_seconds,
    )
    while True:
        try:
            t0 = time.monotonic()
            result = await _run_cycle_for_all_users(client)
            cycle_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "intel_v3.watchtower_worker_entrypoint loop_cycle_summary "
                "users_processed=%d succeeded=%d failed=%d cycle_ms=%d interval_seconds=%s",
                result["users_processed"],
                result["succeeded"],
                result["failed"],
                cycle_ms,
                interval_seconds,
            )
        except Exception as exc:
            logger.exception(
                "intel_v3.watchtower_worker_entrypoint loop pass failed: %s", exc
            )
        await asyncio.sleep(interval_seconds)


def main(argv: "list[str] | None" = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Intel v3 Watchtower Background Refresh Worker",
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
            f"Loop interval in seconds. Overrides {_INTERVAL_ENV}; "
            f"default is {DEFAULT_INTERVAL_SECONDS:g}s."
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
