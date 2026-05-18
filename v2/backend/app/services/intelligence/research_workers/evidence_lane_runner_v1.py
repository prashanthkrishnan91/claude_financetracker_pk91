"""Stage 5F + Stage 5H — Multi-lane evidence population runner (dispatcher/registry).

Wires four feasible evidence lanes into ResearchArtifactServiceV1:
  - fundamentals      → fundamental_quality artifact (yfinance sync)          [Stage 5F]
  - technicals        → technical_signal artifact   (yfinance sync)           [Stage 5F]
  - news_sentiment    → sentiment_event artifact     (yfinance sync)          [Stage 5F]
  - sec_company_facts → fundamental_quality artifact (SEC EDGAR XBRL sync)   [Stage 5H]

Each lane is independently kill-switched via Settings. All writes go through
ResearchArtifactServiceV1.write_artifact(), which injects all four Stage 5
enrichment layers (5B credibility, 5C contradiction, 5D completeness, 5E usability).

Stage 5H provider distinction:
  yfinance  = FREE / UNOFFICIAL_AGGREGATOR — baseline fundamentals lane
  sec_edgar = FREE / OFFICIAL              — official company-facts lane

What this runner NEVER does:
  - Calls decide() or imports the v3 decision policy.
  - Writes to intel_v3_snapshots or any visible-decision table.
  - Uses async providers — all provider calls use sync variants to avoid
    event-loop coupling in the runner context.
  - Runs on page load — explicit callable only.
  - Makes LLM calls.
  - Activates paid providers.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

from app.config import Settings, get_settings

from .contracts import WorkerInput
from .evidence_lane_adapter_v1 import (
    FEASIBLE_LANES,
    LANE_FUNDAMENTALS,
    LANE_TECHNICALS,
    LANE_NEWS_SENTIMENT,
    build_fundamentals_worker_output,
    build_technicals_worker_output,
    build_news_sentiment_worker_output,
)
from .evidence_provider_router_v1 import (
    ROUTE_REASON_NO_PROVIDER,
    resolve_provider_for_lane,
)
from .evidence_provider_registry_v1 import LANE_SEC_COMPANY_FACTS
from .sec_companyfacts_adapter_v1 import build_sec_companyfacts_worker_output
from .sec_metric_candidate_classifier import classify_sec_metric_candidate
from app.services.intelligence.v3.research_artifact_service_v1 import (
    ResearchArtifactServiceV1,
)


# ── Lane dispatch tables ──────────────────────────────────────────────────────

def _is_fundamentals_enabled(s: Settings) -> bool:
    return (
        s.intel_v3_research_workers_enabled
        and s.intel_v3_fundamentals_evidence_enabled
    )


def _is_technicals_enabled(s: Settings) -> bool:
    return (
        s.intel_v3_research_workers_enabled
        and s.intel_v3_technicals_evidence_enabled
    )


def _is_news_sentiment_enabled(s: Settings) -> bool:
    return (
        s.intel_v3_research_workers_enabled
        and s.intel_v3_news_sentiment_evidence_enabled
    )


def _is_sec_companyfacts_enabled(s: Settings) -> bool:
    return (
        s.intel_v3_research_workers_enabled
        and s.intel_v3_sec_companyfacts_evidence_enabled
    )


# ── Per-lane runner functions ─────────────────────────────────────────────────

def run_fundamentals_evidence(
    user_id: str,
    ticker: str,
    db_client: Any,
    parent_intel_run_id: Optional[str] = None,
    holding_context: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
    _fetch_fn: Optional[Callable[[str], dict[str, Any]]] = None,
) -> Optional[str]:
    """Run the fundamentals evidence lane for one ticker.

    Returns artifact_id if written, None if disabled or on error.

    Kill-switch hierarchy:
      1. settings.intel_v3_research_workers_enabled  (global kill switch)
      2. settings.intel_v3_fundamentals_evidence_enabled

    Args:
        _fetch_fn: Injectable sync fetch callable for tests.
                   Signature: (ticker: str) -> dict[str, Any].
                   Defaults to fetch_yfinance_fundamentals_sync.
    """
    if settings is None:
        settings = get_settings()
    if not _is_fundamentals_enabled(settings):
        logger.debug(
            "evidence_lane_skip lane=fundamentals ticker=%s reason=flag_off", ticker
        )
        return None

    # Consult provider registry/router — ensures free-first selection and
    # honest skip when no enabled provider exists for this lane.
    route = resolve_provider_for_lane(LANE_FUNDAMENTALS)
    if route.reason == ROUTE_REASON_NO_PROVIDER:
        logger.warning(
            "evidence_lane_no_provider lane=fundamentals ticker=%s", ticker
        )
        return None
    logger.debug(
        "evidence_lane_provider_resolved lane=fundamentals ticker=%s "
        "provider=%s reason=%s",
        ticker, route.provider_id, route.reason,
    )

    ticker_upper = ticker.upper().strip()
    fetched_at = datetime.now(timezone.utc).isoformat()
    worker_run_id = str(uuid.uuid4())
    worker_input = WorkerInput(
        user_id=user_id,
        ticker=ticker_upper,
        worker_run_id=worker_run_id,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
    )

    if _fetch_fn is not None:
        fetch_fn = _fetch_fn
    else:
        from app.services.agents.data_sources import fetch_yfinance_fundamentals_sync
        fetch_fn = fetch_yfinance_fundamentals_sync

    logger.info(
        "evidence_lane_start lane=fundamentals ticker=%s worker_run_id=%s",
        ticker_upper, worker_run_id,
    )

    try:
        raw = fetch_fn(ticker_upper)
    except Exception as exc:
        logger.warning(
            "evidence_lane_fetch_error lane=fundamentals ticker=%s error=%s",
            ticker_upper, exc,
        )
        raw = {}

    output = build_fundamentals_worker_output(worker_input, raw, fetched_at)
    service = ResearchArtifactServiceV1(supabase_client=db_client, user_id=user_id)
    artifact_id = service.write_artifact(output)

    if artifact_id:
        logger.info(
            "evidence_lane_complete lane=fundamentals ticker=%s artifact_id=%s "
            "confidence=%s freshness=%s",
            ticker_upper, artifact_id,
            output.confidence_or_trust_level, output.freshness_status,
        )
    else:
        logger.warning(
            "evidence_lane_no_artifact lane=fundamentals ticker=%s", ticker_upper
        )
    return artifact_id


def run_technicals_evidence(
    user_id: str,
    ticker: str,
    db_client: Any,
    parent_intel_run_id: Optional[str] = None,
    holding_context: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
    _fetch_fn: Optional[Callable[[str], dict[str, Any]]] = None,
) -> Optional[str]:
    """Run the technicals evidence lane for one ticker.

    Returns artifact_id if written, None if disabled or on error.

    Kill-switch hierarchy:
      1. settings.intel_v3_research_workers_enabled
      2. settings.intel_v3_technicals_evidence_enabled

    Args:
        _fetch_fn: Injectable sync fetch callable for tests.
                   Signature: (ticker: str) -> dict[str, Any].
                   Defaults to fetch_yfinance_history_sync.
    """
    if settings is None:
        settings = get_settings()
    if not _is_technicals_enabled(settings):
        logger.debug(
            "evidence_lane_skip lane=technicals ticker=%s reason=flag_off", ticker
        )
        return None

    route = resolve_provider_for_lane(LANE_TECHNICALS)
    if route.reason == ROUTE_REASON_NO_PROVIDER:
        logger.warning(
            "evidence_lane_no_provider lane=technicals ticker=%s", ticker
        )
        return None
    logger.debug(
        "evidence_lane_provider_resolved lane=technicals ticker=%s "
        "provider=%s reason=%s",
        ticker, route.provider_id, route.reason,
    )

    ticker_upper = ticker.upper().strip()
    fetched_at = datetime.now(timezone.utc).isoformat()
    worker_run_id = str(uuid.uuid4())
    worker_input = WorkerInput(
        user_id=user_id,
        ticker=ticker_upper,
        worker_run_id=worker_run_id,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
    )

    if _fetch_fn is not None:
        fetch_fn = _fetch_fn
    else:
        from app.services.agents.data_sources import fetch_yfinance_history_sync
        fetch_fn = fetch_yfinance_history_sync

    logger.info(
        "evidence_lane_start lane=technicals ticker=%s worker_run_id=%s",
        ticker_upper, worker_run_id,
    )

    try:
        raw = fetch_fn(ticker_upper)
    except Exception as exc:
        logger.warning(
            "evidence_lane_fetch_error lane=technicals ticker=%s error=%s",
            ticker_upper, exc,
        )
        raw = {}

    output = build_technicals_worker_output(worker_input, raw, fetched_at)
    service = ResearchArtifactServiceV1(supabase_client=db_client, user_id=user_id)
    artifact_id = service.write_artifact(output)

    if artifact_id:
        logger.info(
            "evidence_lane_complete lane=technicals ticker=%s artifact_id=%s "
            "confidence=%s freshness=%s",
            ticker_upper, artifact_id,
            output.confidence_or_trust_level, output.freshness_status,
        )
    else:
        logger.warning(
            "evidence_lane_no_artifact lane=technicals ticker=%s", ticker_upper
        )
    return artifact_id


def run_news_sentiment_evidence(
    user_id: str,
    ticker: str,
    db_client: Any,
    parent_intel_run_id: Optional[str] = None,
    holding_context: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
    _fetch_fn: Optional[Callable[[str], list[dict[str, Any]]]] = None,
) -> Optional[str]:
    """Run the news/sentiment evidence lane for one ticker.

    Returns artifact_id if written, None if disabled or on error.

    Kill-switch hierarchy:
      1. settings.intel_v3_research_workers_enabled
      2. settings.intel_v3_news_sentiment_evidence_enabled

    Args:
        _fetch_fn: Injectable sync fetch callable for tests.
                   Signature: (ticker: str) -> list[dict[str, Any]].
                   Defaults to fetch_yfinance_news_sync.
    """
    if settings is None:
        settings = get_settings()
    if not _is_news_sentiment_enabled(settings):
        logger.debug(
            "evidence_lane_skip lane=news_sentiment ticker=%s reason=flag_off", ticker
        )
        return None

    route = resolve_provider_for_lane(LANE_NEWS_SENTIMENT)
    if route.reason == ROUTE_REASON_NO_PROVIDER:
        logger.warning(
            "evidence_lane_no_provider lane=news_sentiment ticker=%s", ticker
        )
        return None
    logger.debug(
        "evidence_lane_provider_resolved lane=news_sentiment ticker=%s "
        "provider=%s reason=%s",
        ticker, route.provider_id, route.reason,
    )

    ticker_upper = ticker.upper().strip()
    fetched_at = datetime.now(timezone.utc).isoformat()
    worker_run_id = str(uuid.uuid4())
    worker_input = WorkerInput(
        user_id=user_id,
        ticker=ticker_upper,
        worker_run_id=worker_run_id,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
    )

    if _fetch_fn is not None:
        fetch_fn = _fetch_fn
    else:
        from app.services.agents.data_sources import fetch_yfinance_news_sync
        fetch_fn = fetch_yfinance_news_sync

    logger.info(
        "evidence_lane_start lane=news_sentiment ticker=%s worker_run_id=%s",
        ticker_upper, worker_run_id,
    )

    try:
        items = fetch_fn(ticker_upper)
    except Exception as exc:
        logger.warning(
            "evidence_lane_fetch_error lane=news_sentiment ticker=%s error=%s",
            ticker_upper, exc,
        )
        items = []

    output = build_news_sentiment_worker_output(worker_input, items, fetched_at)
    service = ResearchArtifactServiceV1(supabase_client=db_client, user_id=user_id)
    artifact_id = service.write_artifact(output)

    if artifact_id:
        logger.info(
            "evidence_lane_complete lane=news_sentiment ticker=%s artifact_id=%s "
            "confidence=%s freshness=%s",
            ticker_upper, artifact_id,
            output.confidence_or_trust_level, output.freshness_status,
        )
    else:
        logger.warning(
            "evidence_lane_no_artifact lane=news_sentiment ticker=%s", ticker_upper
        )
    return artifact_id


def run_sec_companyfacts_evidence(
    user_id: str,
    ticker: str,
    db_client: Any,
    parent_intel_run_id: Optional[str] = None,
    holding_context: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
    _provider_fn: Optional[Callable] = None,
) -> Optional[str]:
    """Run the SEC CompanyFacts official fundamentals lane for one ticker.

    Returns artifact_id if written, None if disabled, no-cik, or on error.

    Kill-switch hierarchy:
      1. settings.intel_v3_research_workers_enabled  (global kill switch)
      2. settings.intel_v3_sec_companyfacts_evidence_enabled
      3. settings.sec_edgar_user_agent must be non-empty

    Provider distinction (Stage 5H):
      This lane uses SEC EDGAR (FREE / OFFICIAL) — distinct from the yfinance
      fundamentals lane. Both lanes write fundamental_quality artifacts but use
      different skill_packs: sec_companyfacts_evidence_v1 vs fundamentals_evidence_v1.
      yfinance remains the free baseline; SEC official lane runs when this flag is on.

    Args:
        _provider_fn: Injectable callable for tests.
                      Signature: (ticker: str) -> SecEdgarProviderResult.
                      Defaults to fetching via sec_edgar_provider.fetch_for_ticker().
    """
    if settings is None:
        settings = get_settings()
    if not _is_sec_companyfacts_enabled(settings):
        logger.debug(
            "evidence_lane_skip lane=sec_company_facts ticker=%s reason=flag_off", ticker
        )
        return None

    # Stage 5H.3 — Non-equity / non-company-ticker eligibility guard.
    # Prevents SEC ticker-symbol collisions from mapping crypto/ETF/fund
    # symbols to unrelated public companies. Uses holding_context metadata
    # when available; falls back to a conservative known-symbol list for
    # common portfolio crypto symbols (BTC, XRP) and ETF symbols.
    category = ""
    if holding_context:
        for k in ("category", "asset_type", "security_type", "instrument_type", "asset_class"):
            v = holding_context.get(k)
            if isinstance(v, str) and v.strip():
                category = v.strip()
                break
    classification = classify_sec_metric_candidate(ticker, category)
    if not classification["is_sec_company_candidate"]:
        skip_source = "metadata" if category else "symbol_fallback"
        logger.info(
            "sec_companyfacts_skip_non_equity ticker=%s classification=%s "
            "category=%s skip_source=%s reason_codes=%s",
            ticker.upper().strip(),
            classification["classification"],
            category or "unspecified",
            skip_source,
            ",".join(classification.get("blocking_reason_codes", [])) or "none",
        )
        return None

    # Consult provider registry/router — ensures free-first selection.
    route = resolve_provider_for_lane(LANE_SEC_COMPANY_FACTS)
    if route.reason == ROUTE_REASON_NO_PROVIDER:
        logger.warning(
            "evidence_lane_no_provider lane=sec_company_facts ticker=%s", ticker
        )
        return None
    logger.debug(
        "evidence_lane_provider_resolved lane=sec_company_facts ticker=%s "
        "provider=%s reason=%s",
        ticker, route.provider_id, route.reason,
    )

    ticker_upper = ticker.upper().strip()
    fetched_at = datetime.now(timezone.utc).isoformat()
    worker_run_id = str(uuid.uuid4())
    worker_input = WorkerInput(
        user_id=user_id,
        ticker=ticker_upper,
        worker_run_id=worker_run_id,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
    )

    if _provider_fn is not None:
        provider_fn = _provider_fn
    else:
        from .sec_edgar_provider import fetch_for_ticker, SecEdgarProviderConfig
        user_agent = settings.sec_edgar_user_agent or ""
        if not user_agent.strip():
            logger.warning(
                "evidence_lane_skip lane=sec_company_facts ticker=%s "
                "reason=no_sec_edgar_user_agent",
                ticker_upper,
            )
            return None
        _cfg = SecEdgarProviderConfig(user_agent=user_agent)
        provider_fn = lambda t: fetch_for_ticker(t, _cfg)  # noqa: E731

    logger.info(
        "evidence_lane_start lane=sec_company_facts ticker=%s worker_run_id=%s",
        ticker_upper, worker_run_id,
    )

    try:
        from .sec_edgar_provider import SecEdgarProviderResult
        provider_result: "SecEdgarProviderResult" = provider_fn(ticker_upper)
    except Exception as exc:
        logger.warning(
            "evidence_lane_fetch_error lane=sec_company_facts ticker=%s error=%s",
            ticker_upper, exc,
        )
        from .sec_edgar_provider import SecEdgarProviderResult
        provider_result = SecEdgarProviderResult(
            ticker=ticker_upper,
            fetch_status="error",
            error_message=f"runner_catch: {exc}",
            fetched_at=fetched_at,
        )

    output = build_sec_companyfacts_worker_output(worker_input, provider_result, fetched_at)

    # Do not write a placeholder NOT_EVALUABLE artifact when there are no XBRL
    # observations. This covers: ETFs, funds, crypto, non-company tickers (no CIK),
    # tickers where companyfacts was not fetched, and parse errors. Writing a zero-
    # observation artifact produces only noise — the evidence gap is already logged.
    obs_count = output.artifact_payload.get("observation_count", 0)
    if obs_count == 0:
        skip_reason = output.artifact_payload.get("fetch_status", "no_observations")
        logger.info(
            "sec_companyfacts_skip_no_artifact ticker=%s reason=%s cik=%s",
            ticker_upper,
            skip_reason,
            output.artifact_payload.get("cik"),
        )
        return None

    service = ResearchArtifactServiceV1(supabase_client=db_client, user_id=user_id)
    artifact_id = service.write_artifact(output)

    if artifact_id:
        payload = output.artifact_payload
        logger.info(
            "sec_companyfacts_artifact_written ticker=%s artifact_id=%s "
            "observation_count=%d tag_count=%d confidence=%s freshness=%s",
            ticker_upper, artifact_id,
            payload.get("observation_count", 0),
            payload.get("tag_count", 0),
            output.confidence_or_trust_level,
            output.freshness_status,
        )

        # Stage 5H.3 — Compact, deterministic SEC CompanyFacts usability
        # summary for runtime debugging. Replays detect_contradictions on the
        # same facts (deterministic, no IO) so we can log the SEC-specific
        # group-key shape without parsing the persisted payload.
        try:
            from app.services.intelligence.v3.contradiction_detector_v1 import (
                detect_contradictions,
            )
            from app.services.intelligence.v3.artifact_truth_adapter_v1 import (
                assess_artifact_usability,
            )
            from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
                score_evidence_completeness,
            )
            from app.services.intelligence.v3.source_credibility_registry_v1 import (
                assess_artifact_sources,
            )
            cred = assess_artifact_sources(output.sources)
            contra = detect_contradictions(output.facts)
            comp = score_evidence_completeness(
                sources=output.sources, facts=output.facts,
                credibility_assessment=cred, contradiction_assessment=contra,
            )
            usab = assess_artifact_usability(cred, contra, comp)
            sample_keys = [g.get("group_key", "") for g in contra.contradiction_groups[:3]]
            logger.info(
                "sec_companyfacts_usability_summary ticker=%s observation_count=%d "
                "contradiction_count=%d usability_label=%s sample_group_keys=%s",
                ticker_upper,
                payload.get("observation_count", 0),
                contra.contradiction_count,
                usab.usability_label,
                ";".join(sample_keys) or "none",
            )
        except Exception as _exc:  # noqa: BLE001
            logger.debug(
                "sec_companyfacts_usability_summary_failed ticker=%s error=%s",
                ticker_upper, _exc,
            )
    else:
        logger.warning(
            "evidence_lane_no_artifact lane=sec_company_facts ticker=%s", ticker_upper
        )
    return artifact_id


# ── Dispatcher: run all feasible lanes ───────────────────────────────────────

def run_all_evidence_lanes(
    user_id: str,
    ticker: str,
    db_client: Any,
    parent_intel_run_id: Optional[str] = None,
    holding_context: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
    _fundamentals_fetch_fn: Optional[Callable] = None,
    _technicals_fetch_fn: Optional[Callable] = None,
    _news_sentiment_fetch_fn: Optional[Callable] = None,
    _sec_companyfacts_provider_fn: Optional[Callable] = None,
) -> dict[str, Optional[str]]:
    """Run all feasible evidence lanes for one ticker.

    Returns a dict mapping lane name → artifact_id (or None if disabled/failed).
    Lanes that are disabled return None; no exception propagates.

    Usage (production):
        result = run_all_evidence_lanes(user_id, ticker, db_client, settings=settings)

    Usage (tests — inject fakes to avoid real HTTP calls):
        result = run_all_evidence_lanes(
            user_id, ticker, db_client, settings=settings,
            _fundamentals_fetch_fn=lambda t: {...},
            _technicals_fetch_fn=lambda t: {...},
            _news_sentiment_fetch_fn=lambda t: [...],
            _sec_companyfacts_provider_fn=lambda t: <SecEdgarProviderResult>,
        )
    """
    results: dict[str, Optional[str]] = {}

    results[LANE_FUNDAMENTALS] = run_fundamentals_evidence(
        user_id=user_id,
        ticker=ticker,
        db_client=db_client,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
        settings=settings,
        _fetch_fn=_fundamentals_fetch_fn,
    )
    results[LANE_TECHNICALS] = run_technicals_evidence(
        user_id=user_id,
        ticker=ticker,
        db_client=db_client,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
        settings=settings,
        _fetch_fn=_technicals_fetch_fn,
    )
    results[LANE_NEWS_SENTIMENT] = run_news_sentiment_evidence(
        user_id=user_id,
        ticker=ticker,
        db_client=db_client,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
        settings=settings,
        _fetch_fn=_news_sentiment_fetch_fn,
    )
    results[LANE_SEC_COMPANY_FACTS] = run_sec_companyfacts_evidence(
        user_id=user_id,
        ticker=ticker,
        db_client=db_client,
        parent_intel_run_id=parent_intel_run_id,
        holding_context=holding_context,
        settings=settings,
        _provider_fn=_sec_companyfacts_provider_fn,
    )

    enabled_count = sum(1 for v in results.values() if v is not None)
    logger.info(
        "evidence_lane_dispatcher_complete ticker=%s lanes_enabled=%d lanes_written=%d results=%s",
        ticker.upper().strip(),
        enabled_count,
        sum(1 for v in results.values() if v is not None),
        {k: ("written" if v else "skipped") for k, v in results.items()},
    )
    return results
