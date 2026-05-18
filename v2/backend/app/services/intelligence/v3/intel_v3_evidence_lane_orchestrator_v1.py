"""Stage 5H.1 — Intel v3 explicit-run evidence lane orchestrator.

Called fire-and-forget from enqueue_run_v3() on every explicit POST /intel/v3/run.
Runs all enabled evidence lanes (5F + 5H) for all portfolio tickers, regardless of
whether analyst evidence is already current.

Contract:
  - Sync function; the caller wraps in asyncio.to_thread for non-blocking dispatch.
  - Respects all flag gates (global + per-lane); disabled lanes skip immediately.
  - Never called from GET /intel/v3/snapshot (page-load contract preserved).
  - Never writes to intel_v3_snapshots or recommendations.
  - Never calls decide().
  - No paid providers. No LLM calls.

Log keys emitted:
  intel_v3_evidence_lanes_dispatch_start   total_tickers=N user_id=... parent_intel_run_id=...
  intel_v3_evidence_lanes_dispatch_complete tickers_attempted=N artifacts_written=N skipped=N
  evidence_lane_start/complete              (emitted by per-lane runners in evidence_lane_runner_v1)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.config import Settings, get_settings


def run_enabled_evidence_lanes_for_portfolio(
    user_id: str,
    tickers: list[str],
    db_client: Any,
    parent_intel_run_id: Optional[str] = None,
    settings: Optional[Settings] = None,
    holding_context_by_ticker: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, dict[str, Optional[str]]]:
    """Run all enabled evidence lanes for every portfolio ticker.

    Returns {ticker: {lane_name: artifact_id_or_None}}.
    Safe to call unconditionally — disabled global flag is a fast no-op.

    Explicit-run-only contract: the caller (enqueue_run_v3) fires this via
    asyncio.to_thread so the 202 response is not delayed. GET /snapshot does not
    call this function.
    """
    if settings is None:
        settings = get_settings()

    if not settings.intel_v3_research_workers_enabled:
        logger.debug(
            "intel_v3_evidence_lanes_dispatch_skip reason=global_flag_off total_tickers=%d",
            len(tickers),
        )
        return {}

    if not tickers:
        logger.debug("intel_v3_evidence_lanes_dispatch_skip reason=no_tickers")
        return {}

    logger.info(
        "intel_v3_evidence_lanes_dispatch_start total_tickers=%d user_id=%s "
        "parent_intel_run_id=%s",
        len(tickers),
        user_id,
        parent_intel_run_id or "none",
    )

    from app.services.intelligence.research_workers.runner import (
        run_evidence_lanes_for_ticker,
    )

    all_results: dict[str, dict[str, Optional[str]]] = {}
    total_artifacts_written = 0
    total_skipped = 0

    ctx_map = holding_context_by_ticker or {}
    for ticker in tickers:
        try:
            results = run_evidence_lanes_for_ticker(
                user_id=user_id,
                ticker=ticker,
                db_client=db_client,
                parent_intel_run_id=parent_intel_run_id,
                holding_context=ctx_map.get(ticker) or ctx_map.get(ticker.upper().strip()),
                settings=settings,
            )
            all_results[ticker] = results
            written = sum(1 for v in results.values() if v is not None)
            skipped = sum(1 for v in results.values() if v is None)
            total_artifacts_written += written
            total_skipped += skipped
        except Exception as exc:
            logger.warning(
                "intel_v3_evidence_lane_ticker_error ticker=%s error=%s",
                ticker,
                exc,
            )
            all_results[ticker] = {}
            total_skipped += 1

    logger.info(
        "intel_v3_evidence_lanes_dispatch_complete tickers_attempted=%d "
        "artifacts_written=%d skipped=%d parent_intel_run_id=%s",
        len(tickers),
        total_artifacts_written,
        total_skipped,
        parent_intel_run_id or "none",
    )

    return all_results
