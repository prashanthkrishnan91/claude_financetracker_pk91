"""Phase 3 research worker runner — env-gated dark-run entrypoint.

Usage:
    from app.services.intelligence.research_workers.runner import (
        run_earnings_reviewer_dark,
    )
    artifact_id = run_earnings_reviewer_dark(
        user_id="...", ticker="AAPL", db_client=supabase_client
    )

Contract:
  - Returns None immediately if either kill switch is off (safe to call unconditionally).
  - Never runs on page load — this is an explicit callable, never a side-effect of a
    request handler unless an operator explicitly invokes the route/task.
  - Never imports or calls decide().
  - Never writes to intel_v3_snapshots.
  - All DB errors are caught; returns None on failure without propagating.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.config import Settings, get_settings

from .artifact_store_writer import ArtifactStoreWriter
from .contracts import WorkerInput
from . import earnings_reviewer


def run_earnings_reviewer_dark(
    user_id: str,
    ticker: str,
    db_client: Any,
    parent_intel_run_id: Optional[str] = None,
    holding_context: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> Optional[str]:
    """Run the Earnings Reviewer dark-run scaffold for one ticker.

    Returns the artifact_id (str) if written, None if disabled or on error.
    Safe to call at any time — disabled flags make it a fast no-op.

    Kill-switch hierarchy (both must be True to run):
      1. settings.intel_v3_research_workers_enabled  (global kill switch)
      2. settings.intel_v3_earnings_reviewer_enabled (per-worker kill switch)
    """
    if settings is None:
        settings = get_settings()

    if not settings.intel_v3_research_workers_enabled:
        logger.debug(
            "research_worker_skip reason=global_flag_off worker=earnings_reviewer ticker=%s",
            ticker,
        )
        return None

    if not settings.intel_v3_earnings_reviewer_enabled:
        logger.debug(
            "research_worker_skip reason=worker_flag_off worker=earnings_reviewer ticker=%s",
            ticker,
        )
        return None

    worker_run_id = str(uuid.uuid4())
    worker_input = WorkerInput(
        user_id=user_id,
        ticker=ticker,
        worker_run_id=worker_run_id,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
    )

    logger.info(
        "research_worker_start worker=earnings_reviewer ticker=%s worker_run_id=%s",
        ticker,
        worker_run_id,
    )

    output = earnings_reviewer.run(worker_input)

    writer = ArtifactStoreWriter(supabase_client=db_client, user_id=user_id)
    artifact_id = writer.write(output)

    if artifact_id:
        logger.info(
            "research_worker_complete worker=earnings_reviewer ticker=%s "
            "artifact_id=%s replay_key=%s",
            ticker,
            artifact_id,
            output.replay_idempotency_key,
        )
    else:
        logger.warning(
            "research_worker_no_artifact worker=earnings_reviewer ticker=%s",
            ticker,
        )

    return artifact_id
