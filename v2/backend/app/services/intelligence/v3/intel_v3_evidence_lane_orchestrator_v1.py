"""Stage 5H.1 + Stage 5I — Intel v3 explicit-run evidence lane orchestrator.

Called fire-and-forget from enqueue_run_v3() on every explicit POST /intel/v3/run.
Runs all enabled evidence lanes (5F + 5H per-ticker + 5I portfolio macro),
regardless of whether analyst evidence is already current.

Contract:
  - Sync function; the caller wraps in asyncio.to_thread for non-blocking dispatch.
  - Respects all flag gates (global + per-lane); disabled lanes skip immediately.
  - Never called from GET /intel/v3/snapshot (page-load contract preserved).
  - Never writes to intel_v3_snapshots or recommendations.
  - Never calls decide().
  - No paid providers. No LLM calls.

Stage 5I — FRED macro lane:
  - Portfolio-scope, ticker-agnostic — runs once per explicit dispatch, not per ticker.
  - Gated by settings.intel_v3_macro_evidence_enabled + settings.fred_api_key.
  - Failure is fail-soft; per-ticker lane dispatch is unaffected.

Log keys emitted:
  intel_v3_evidence_lanes_dispatch_start   total_tickers=N user_id=... parent_intel_run_id=...
  intel_v3_evidence_lanes_dispatch_complete tickers_attempted=N artifacts_written=N skipped=N
                                            macro_artifact_id=...
  evidence_lane_start/complete              (emitted by per-lane runners in evidence_lane_runner_v1)
  fred_macro_evidence_start/complete        (emitted by run_fred_macro_evidence)
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

    logger.info(
        "intel_v3_evidence_lanes_dispatch_start total_tickers=%d user_id=%s "
        "parent_intel_run_id=%s",
        len(tickers),
        user_id,
        parent_intel_run_id or "none",
    )

    all_results: dict[str, dict[str, Optional[str]]] = {}
    total_artifacts_written = 0
    total_skipped = 0

    # Per-ticker lanes run only when at least one ticker is supplied. Stage 5I
    # portfolio-scope macro lane below still runs even when tickers=[] (e.g.,
    # empty portfolio explicit run).
    if tickers:
        from app.services.intelligence.research_workers.runner import (
            run_evidence_lanes_for_ticker,
        )

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

    # Stage 5I — Portfolio-scope macro lane (FRED). Dispatched once per explicit
    # run, not per ticker. Fail-soft: per-ticker lanes above are unaffected by any
    # macro-lane failure here. Gate-checked inside run_fred_macro_evidence — safe
    # to call unconditionally (returns None when flag off or api key missing).
    macro_artifact_id: Optional[str] = None
    try:
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_fred_macro_evidence,
        )
        macro_artifact_id = run_fred_macro_evidence(
            user_id=user_id,
            db_client=db_client,
            parent_intel_run_id=parent_intel_run_id,
            settings=settings,
        )
        if macro_artifact_id is not None:
            total_artifacts_written += 1
        else:
            total_skipped += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intel_v3_evidence_lane_macro_error error=%s",
            exc,
        )
        total_skipped += 1

    logger.info(
        "intel_v3_evidence_lanes_dispatch_complete tickers_attempted=%d "
        "artifacts_written=%d skipped=%d macro_artifact_id=%s parent_intel_run_id=%s",
        len(tickers),
        total_artifacts_written,
        total_skipped,
        macro_artifact_id or "none",
        parent_intel_run_id or "none",
    )

    # Stage 5J + 5K — post-lane readiness evaluation (unconditional, fail-soft).
    # Reads active research_artifacts; idempotency-skipped existing artifacts are
    # in the active set and count as valid evidence inputs even when no new writes
    # occurred this run. Emits sec_catalyst_stage5j_readiness (Stage 5J),
    # sentiment_stage5k_source_selection (Stage 5K), and snapshot_sentiment_readiness
    # for usable sec_catalyst_sentiment lanes. Runs regardless of republisher
    # decisions — diagnostics appear even when the certified snapshot is current.
    try:
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            LANE_SEC_CATALYST_SENTIMENT as _LANE_SEC_CATALYST,
            compute_research_evidence_coverage,
            log_coverage_summary,
        )
        from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
            AXIS_SENTIMENT as _AXIS_SENTIMENT,
            compute_decision_input_readiness,
        )

        _coverage = compute_research_evidence_coverage(
            user_id=user_id,
            tickers=tickers,
            db_client=db_client,
        )
        log_coverage_summary(_coverage)

        _shadow = compute_decision_input_readiness(
            _coverage,
            holding_context_by_ticker=holding_context_by_ticker,
        )

        for _ticker, _tr in _shadow.ticker_readiness.items():
            _sent_axis = _tr.axes.get(_AXIS_SENTIMENT)
            if _sent_axis is not None and _sent_axis.is_usable:
                _sent_source = (
                    "sec_catalyst_sentiment"
                    if _LANE_SEC_CATALYST in (_sent_axis.contributing_lanes or [])
                    else "news_sentiment"
                )
                logger.info(
                    "snapshot_sentiment_readiness ticker=%s status=%s source=%s",
                    _ticker,
                    _sent_axis.readiness,
                    _sent_source,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intel_v3_evidence_stage5j_5k_post_lane_error error=%s",
            exc,
        )

    return all_results
