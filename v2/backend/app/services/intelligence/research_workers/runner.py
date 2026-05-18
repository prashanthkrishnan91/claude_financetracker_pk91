"""Phase 3 / Phase 6A research worker runner — env-gated dark-run entrypoint.

Usage:
    from app.services.intelligence.research_workers.runner import (
        run_earnings_reviewer_dark,
    )
    artifact_id = run_earnings_reviewer_dark(
        user_id="...", ticker="AAPL", db_client=supabase_client
    )

Contract:
  - Returns None immediately if any required kill switch is off (safe to call unconditionally).
  - Never runs on page load — this is an explicit callable only.
  - Never imports or calls decide().
  - Never writes to intel_v3_snapshots.
  - All DB errors are caught; returns None on failure without propagating.

Phase 6A SEC gate (in addition to Phase 3 flags):
  - settings.intel_v3_earnings_reviewer_sec_enabled controls whether SEC EDGAR
    evidence population is attempted.
  - settings.sec_edgar_user_agent must be non-empty; if missing, SEC fetch is
    skipped (fail-closed: earnings_reviewer.run() called with sec_config=None →
    Phase 3 behavior, limitation recorded by provider/adapter).
  - If SEC flag is on and user_agent is set, SecEdgarProviderConfig is built and
    passed to earnings_reviewer.run(). The worker handles all SEC failures internally.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

from app.config import Settings, get_settings

from .contracts import WorkerInput
from . import earnings_reviewer
from app.services.intelligence.v3.research_artifact_service_v1 import (
    ResearchArtifactServiceV1,
)


def run_evidence_lanes_for_ticker(
    user_id: str,
    ticker: str,
    db_client: Any,
    parent_intel_run_id: Optional[str] = None,
    holding_context: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
    _fundamentals_fetch_fn: Optional[Callable] = None,
    _technicals_fetch_fn: Optional[Callable] = None,
    _news_sentiment_fetch_fn: Optional[Callable] = None,
) -> dict[str, Optional[str]]:
    """Run all Stage 5F evidence lanes for one ticker.

    Explicit callable only — never runs on page load.
    Returns a dict of {lane_name: artifact_id_or_None}.
    Safe to call unconditionally — disabled flags skip immediately.

    Kill-switch hierarchy:
      1. settings.intel_v3_research_workers_enabled  (global kill switch)
      Per-lane flags checked by the dispatcher:
      2. settings.intel_v3_fundamentals_evidence_enabled
      3. settings.intel_v3_technicals_evidence_enabled
      4. settings.intel_v3_news_sentiment_evidence_enabled

    Args:
        _fundamentals_fetch_fn / _technicals_fetch_fn / _news_sentiment_fetch_fn:
            Injectable sync fetch callables for tests.  Omit in production.
    """
    if settings is None:
        settings = get_settings()

    if not settings.intel_v3_research_workers_enabled:
        logger.debug(
            "stage5f_evidence_lanes_skip reason=global_flag_off ticker=%s", ticker
        )
        return {}

    from .evidence_lane_runner_v1 import run_all_evidence_lanes

    results = run_all_evidence_lanes(
        user_id=user_id,
        ticker=ticker,
        db_client=db_client,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
        settings=settings,
        _fundamentals_fetch_fn=_fundamentals_fetch_fn,
        _technicals_fetch_fn=_technicals_fetch_fn,
        _news_sentiment_fetch_fn=_news_sentiment_fetch_fn,
    )

    written = sum(1 for v in results.values() if v is not None)
    logger.info(
        "stage5f_evidence_lanes_complete ticker=%s lanes_written=%d/%d results=%s",
        ticker.upper().strip(),
        written,
        len(results),
        {k: ("written" if v else "skipped") for k, v in results.items()},
    )
    return results


def run_earnings_reviewer_dark(
    user_id: str,
    ticker: str,
    db_client: Any,
    parent_intel_run_id: Optional[str] = None,
    holding_context: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
    _http_get_fn: Optional[Callable[[str], Any]] = None,
) -> Optional[str]:
    """Run the Earnings Reviewer dark-run worker for one ticker.

    Returns the artifact_id (str) if written, None if disabled or on error.
    Safe to call at any time — disabled flags make it a fast no-op.

    Kill-switch hierarchy (all must be True to run):
      1. settings.intel_v3_research_workers_enabled  (global kill switch)
      2. settings.intel_v3_earnings_reviewer_enabled (per-worker kill switch)

    Optional Phase 6A SEC path (enabled independently):
      3. settings.intel_v3_earnings_reviewer_sec_enabled  (SEC evidence flag)
      4. settings.sec_edgar_user_agent non-empty           (required for SEC calls)

    Args:
        _http_get_fn: Injectable GET callable for tests. Passed to earnings_reviewer.run()
                      which passes it to sec_edgar_provider.fetch_for_ticker(). Only used
                      when the SEC path is active.
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

    # Build SEC config if the evidence population flag is enabled.
    sec_config = None
    if settings.intel_v3_earnings_reviewer_sec_enabled:
        user_agent = settings.sec_edgar_user_agent or ""
        if not user_agent.strip():
            logger.warning(
                "research_worker_sec_skip reason=no_user_agent ticker=%s "
                "intel_v3_earnings_reviewer_sec_enabled=True but sec_edgar_user_agent "
                "is empty — falling back to dark-run scaffold",
                ticker,
            )
            # sec_config remains None → Phase 3 behavior.
            # The SEC provider would also gate on empty user_agent, so passing it
            # would produce a fail-closed artifact. Falling back to Phase 3 is
            # equivalent and avoids an unnecessary SEC artifact row.
        else:
            from .sec_edgar_provider import SecEdgarProviderConfig
            sec_config = SecEdgarProviderConfig(user_agent=user_agent)
            logger.debug(
                "research_worker_sec_enabled ticker=%s user_agent_set=True",
                ticker,
            )

    worker_run_id = str(uuid.uuid4())
    worker_input = WorkerInput(
        user_id=user_id,
        ticker=ticker,
        worker_run_id=worker_run_id,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
    )

    logger.info(
        "research_worker_start worker=earnings_reviewer ticker=%s worker_run_id=%s "
        "sec_enabled=%s",
        ticker,
        worker_run_id,
        sec_config is not None,
    )

    output = earnings_reviewer.run(
        worker_input,
        sec_config=sec_config,
        _http_get_fn=_http_get_fn,
    )

    service = ResearchArtifactServiceV1(supabase_client=db_client, user_id=user_id)
    artifact_id = service.write_artifact(output)

    if artifact_id:
        logger.info(
            "research_worker_complete worker=earnings_reviewer ticker=%s "
            "artifact_id=%s replay_key=%s confidence=%s freshness=%s",
            ticker,
            artifact_id,
            output.replay_idempotency_key,
            output.confidence_or_trust_level,
            output.freshness_status,
        )
    else:
        logger.warning(
            "research_worker_no_artifact worker=earnings_reviewer ticker=%s",
            ticker,
        )

    return artifact_id
