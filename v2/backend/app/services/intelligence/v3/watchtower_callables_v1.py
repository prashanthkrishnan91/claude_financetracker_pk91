"""Shared production-default callable builders for Watchtower refresh workers.

Used by:
  - watchtower_worker_entrypoint.py  (scheduled/continuous background loop)
  - intel_v3_service.enqueue_run_v3  (urgent fire-and-forget on stale price/weight)

Keeping builders here avoids duplicating PriceService wiring and prevents the
service layer from importing the entrypoint module.

Pure function-factory module — no IO, no side effects at import time.
"""
from __future__ import annotations

import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID


def build_default_price_refresh_callable(client: Any) -> Optional[Any]:
    """Return a price refresh coroutine callable wired to PriceService.

    Returns None if settings or PriceService are unavailable (test / bare env).
    The callable deduplicates tickers, creates a fresh PriceService per call,
    and closes it on completion.
    """
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


def build_default_analyst_enqueue_callable(client: Any) -> Any:
    """Return an analyst-job enqueue coroutine callable backed by enqueue_refresh_jobs."""
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


def build_default_intel_republish_callable(client: Any) -> Any:
    """Return a coroutine callable that deterministically rebuilds the Intel v3 snapshot.

    Wraps IntelV3Service.run_prewarm_snapshot() — zero LLM calls, no analyst jobs.
    Called after Watchtower price refresh to republish the Intel snapshot with
    fresh evidence. Accepts user_id; triggers the all-or-nothing certification contract.

    The callable is injected into compare_and_republish() to preserve the Watchtower
    worker boundary (this module may import IntelV3Service; the worker may not).
    """
    async def _republish(user_id: UUID) -> Any:
        from .intel_v3_service import IntelV3Service
        svc = IntelV3Service(user_id=user_id)
        prewarm_run_id = str(_uuid_mod.uuid4())
        return await svc.run_prewarm_snapshot(prewarm_run_id=prewarm_run_id)

    return _republish
