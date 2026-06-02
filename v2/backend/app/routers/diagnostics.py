"""Diagnostics router — env-gated runtime certification harness for Finance Intel."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from ..config import get_settings
from ..database import get_supabase_client
from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.recommendation import AgentInsight, AgentRunStatus
from ..services.agents.job_runner import run_agent_pipeline
from ..services.intelligence.research_workers.artifact_observability import summarize_recent_research_artifacts
from ..services.intelligence.research_workers.sec_metric_coverage_expansion import (
    MAX_TICKERS_PER_EXPANSION,
    compute_coverage_expansion,
)
from ..services.intelligence.research_workers.sec_metric_portfolio_coverage_dry_run import compute_portfolio_sec_metric_coverage
from ..services.intelligence.research_workers.sec_metric_evidence_readiness_adapter import (
    compute_sec_metric_evidence_readiness,
    compute_sec_readiness_for_phase11_adapter,
)
from ..services.intelligence.research_workers.validation_harness import run_validation
from ..services.intelligence.v3.evidence_source_registry import build_registry_summary
from ..services.intelligence.v3.sec_metric_truth_adapter_v1 import (
    check_governance_gate,
    build_sec_fundamentals_signal,
    SEC_METRIC_TRUTH_ADAPTER_V1_CONTRACT_VERSION,
)
from ..services.intelligence.v3.valuation_context_adapter_v1 import (
    check_governance_gate as check_valuation_governance_gate,
    build_valuation_context_signal,
    VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION,
    ValuationSignalStatus,
)
from ..services.intelligence.v3.valuation_data_audit_v1 import (
    build_valuation_data_audit,
    VALUATION_DATA_AUDIT_V1_CONTRACT_VERSION,
)
from ..services.intelligence.v3.valuation_input_verification_v1 import (
    build_valuation_input_verification,
    VALUATION_INPUT_VERIFICATION_V1_CONTRACT_VERSION,
)
from ..services.intelligence.v3.price_sector_source_resolution_v1 import (
    build_price_sector_source_resolution,
    PriceCandidateStats,
    SectorCandidateStats,
    PRICE_SECTOR_SOURCE_RESOLUTION_V1_CONTRACT_VERSION,
    PRICE_STALE_THRESHOLD_DAYS as PSR_PRICE_STALE_THRESHOLD_DAYS,
    PRICE_CANDIDATE_PRICE_HISTORY,
    PRICE_CANDIDATE_MARKET_SNAPSHOTS,
    PRICE_CANDIDATE_AGENT_FEATURES,
    PRICE_CANDIDATE_POSITIONS_DERIVED,
    SECTOR_CANDIDATE_MARKET_SNAPSHOTS,
    SECTOR_CANDIDATE_AGENT_FEATURES,
    SECTOR_CANDIDATE_INTEL_V3_PAYLOAD_BLOB,
    SECTOR_CANDIDATE_POSITIONS_CATEGORY,
)
from ..services.intelligence.v3.fy_eps_earnings_yield_v1 import (
    build_fy_eps_earnings_yield,
    EarningsYieldInputRecord,
    FY_EPS_EARNINGS_YIELD_V1_CONTRACT_VERSION,
    PRICEBAND_READY_MIN_COMPUTED_RATIO as _PB_READY_COMPUTED_RATIO,  # noqa: F401
)
from ..services.intelligence.v3.eps_payload_extractor_v1 import (
    extract_fy_eps_observation_from_payload,
    EPS_EXTRACTION_SCHEMA_VERSION,
    SKIP_NOT_FY,
    SKIP_MISSING_YEAR,
    SKIP_MISSING_VALUE,
    SKIP_NOT_SOURCE_LINKED,
)
from ..services.intelligence.v3.ticker_fy_eps_gap_classifier_v1 import (
    build_ticker_fy_eps_gap_diagnostics,
    classify_ticker_fy_eps_gap,
    TickerFyEpsGapInput,
    TICKER_FY_EPS_GAP_CLASSIFIER_V1_CONTRACT_VERSION,
    COMPANY_CLASS_SEC_COMPANY,
)
from ..services.intelligence.v3.fy_eps_raw_trace_v1 import (
    build_fy_eps_raw_trace,
    FyEpsRawTraceInput,
    FY_EPS_RAW_TRACE_V1_CONTRACT_VERSION,
)
from ..services.intelligence.v3.priceband_shadow_policy_v1 import (
    build_priceband_shadow,
    PriceBandShadowInput,
    PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
    PRICEBAND_POLICY_TABLE_ID,
    PRICEBAND_POLICY_BASIS,
)
from ..services.recommendation_engine import RecommendationService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/diagnostics/finance-intel", tags=["diagnostics"])

# Phase 3.6 — endpoint-layer ticker cap (stricter than harness default of 5).
MAX_VALIDATE_TICKERS_PER_REQUEST: int = 3

# Phase 4 — observability endpoint caps.
MAX_OBSERVE_TICKERS_PER_REQUEST: int = 10
MAX_OBSERVE_LOOKBACK_DAYS: int = 365
MIN_OBSERVE_LOOKBACK_DAYS: int = 1
MAX_OBSERVE_ROWS: int = 1000
MIN_OBSERVE_ROWS: int = 1


class FinanceRuntimeCertRequest(BaseModel):
    mode: Literal["read_only_cards", "force_run_agents", "nonforced_run_agents"]
    deposit_amount: float | None = None
    sale_proceeds: float = 0.0


class ResearchWorkersValidateRequest(BaseModel):
    """Phase 3.6 — operator request body for dark-run validation."""
    tickers: list[str] = []


class ResearchArtifactsObserveRequest(BaseModel):
    """Phase 4 — operator request body for read-only artifact observability."""
    tickers: list[str] = []
    lookback_days: int = 30
    max_rows: int = 250


class SecMetricCoverageExpansionRequest(BaseModel):
    """Phase 8E — operator request body for SEC coverage expansion."""
    max_tickers: int = MAX_TICKERS_PER_EXPANSION
    include_tickers: list[str] = []
    exclude_tickers: list[str] = []
    dry_run: bool = False


# Phase 14C.2 — max tickers per backfill request (explicit, safe cap).
_MAX_FY_EPS_BACKFILL_TICKERS: int = 5

# Phase 14C.4 — max tickers per raw trace request (explicit, safe cap).
_MAX_FY_EPS_RAW_TRACE_TICKERS: int = 5


class SecFyEpsBackfillRequest(BaseModel):
    """Phase 14C.2 — operator request body for SEC FY EPS coverage backfill.

    Re-runs the earnings reviewer for an explicit list of tickers so that
    research_artifact_facts is regenerated with the FY EPS coverage policy.
    dry_run=True (default): returns what would be re-run without writing.
    dry_run=False: calls run_earnings_reviewer_dark for each ticker.
    """
    tickers: list[str] = []
    dry_run: bool = True


class FyEpsRawTraceRequest(BaseModel):
    """Phase 14C.4 — operator request body for FY EPS raw trace diagnostic.

    For each explicitly requested ticker, traces exactly where in the data
    pipeline annual FY EPS is lost between raw SEC EDGAR companyfacts and the
    Phase 14C earnings-yield extractor.

    tickers:                 Explicit list of tickers. Max 5 per request.
    include_raw_counts_only: When True (default), attempt a raw SEC EDGAR
                             companyfacts fetch for each ticker to produce
                             unfiltered FY EPS counts. Requires
                             SEC_EDGAR_USER_AGENT to be configured. If the
                             user agent is absent, the fetch is skipped and
                             raw_companyfacts_fetch_status is "no_user_agent".
                             Set to False to restrict analysis to stored DB
                             data only (no external HTTP calls).
    """
    tickers: list[str]
    include_raw_counts_only: bool = True


def _ensure_cert_enabled(secret_header: str | None) -> None:
    settings = get_settings()
    if not settings.finance_runtime_cert_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not settings.finance_runtime_cert_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if not secret_header or not hmac.compare_digest(secret_header, settings.finance_runtime_cert_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


async def _get_runtime_cert_user(
    request: Request,
    cert_secret: str | None = Header(default=None, alias="X-Finance-Runtime-Cert-Secret"),
) -> AuthenticatedUser:
    _ensure_cert_enabled(cert_secret)
    settings = get_settings()

    auth_header = request.headers.get("authorization")
    if auth_header:
        return await get_current_user(request)

    if not settings.finance_runtime_cert_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Runtime certification user is not configured")

    try:
        cert_user_id = UUID(settings.finance_runtime_cert_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Runtime certification user is not configured") from exc

    return AuthenticatedUser(
        user_id=cert_user_id,
        email=settings.finance_runtime_cert_user_email or "runtime-cert@local",
        role="owner",
    )


def _count_language_conflicts(cards: list[Any]) -> dict[str, int]:
    buy_hold = hold_buy = trim_sell_buy = conflicts = 0
    for card in cards:
        action = (getattr(card, "action", "") or "").upper()
        corpus = " ".join(
            str(getattr(card, f, "") or "") for f in ["detail", "rationale", "summary", "reasoning_summary", "action_reason"]
        ).lower()
        has_buy = "buy" in corpus
        has_hold = "hold" in corpus
        if action == "BUY" and has_hold:
            buy_hold += 1
            conflicts += 1
        if action == "HOLD" and has_buy:
            hold_buy += 1
            conflicts += 1
        if action in {"TRIM", "SELL"} and has_buy:
            trim_sell_buy += 1
            conflicts += 1
    return {
        "conflict_count_after_sanitize": conflicts,
        "buy_cards_with_hold_language_count_after_sanitize": buy_hold,
        "hold_cards_with_buy_language_count_after_sanitize": hold_buy,
        "trim_sell_cards_with_buy_language_count_after_sanitize": trim_sell_buy,
    }


def _status_for_mode(mode: str, total_cards: int, thesis: dict[str, int], counters: dict[str, int], cache: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if mode == "read_only_cards":
        if total_cards <= 0:
            reasons.append("no_cards")
        if any(counters[k] > 0 for k in counters):
            reasons.append("narrative_conflicts_detected")
    elif mode == "force_run_agents":
        insufficient = int(thesis.get("insufficient", 0) + thesis.get("INSUFFICIENT_DATA", 0))
        ready_or_partial = int(thesis.get("ready", 0) + thesis.get("partial", 0) + thesis.get("READY", 0) + thesis.get("PARTIAL", 0))
        if insufficient >= max(total_cards, 1):
            reasons.append("all_thesis_insufficient")
        if ready_or_partial <= 0:
            reasons.append("no_ready_or_partial_thesis")
        if counters["conflict_count_after_sanitize"] > 0:
            reasons.append("narrative_conflicts_after_sanitize")
    else:
        if cache.get("cache_reuse_candidate_count") is None:
            return "INCONCLUSIVE", ["cache_reuse_not_observable_yet"]
        rejected = int(cache.get("cache_reuse_rejected_count") or 0)
        accepted = int(cache.get("cache_reuse_accepted_count") or 0)
        skipped = int(cache.get("skipped_fresh_verdicts") or 0)
        reasons_arr = cache.get("cache_reuse_rejection_reasons") or []
        if accepted <= 0 and skipped <= 0 and not reasons_arr:
            reasons.append("cache_reuse_not_observable")
        if rejected > 0 and all(str(r) == "missing_fingerprint" for r in reasons_arr):
            reasons.append("all_rejected_missing_fingerprint")
    return ("PASS", []) if not reasons else ("FAIL", reasons)


@router.post("/certify")
async def certify_finance_runtime(
    payload: FinanceRuntimeCertRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    started = datetime.now(timezone.utc)
    service = RecommendationService(user_id=user.id)

    if payload.mode == "read_only_cards":
        cards = await service.get_insight_cards()
        action_counts = dict(Counter((c.action or "UNKNOWN") for c in cards))
        thesis_counts = dict(Counter(str((c.thesis_v2 or {}).get("status") or "unknown") for c in cards))
        counters = _count_language_conflicts(cards)
        narrative_present = sum(1 for c in cards if any(getattr(c, f, None) for f in ["summary", "reasoning_summary", "detail"]))
        cert = {
            "mode": payload.mode,
            "run_id": None,
            "total_cards": len(cards),
            "action_counts": action_counts,
            "thesis_status_counts": thesis_counts,
            "narrative_contract_present_count": narrative_present,
            **counters,
            "attempted_llm_calls": 0,
            "successful_llm_calls": 0,
            "skipped_fresh_verdicts": 0,
            "cache_reuse_candidate_count": 0,
            "cache_reuse_accepted_count": 0,
            "cache_reuse_rejected_count": 0,
            "cache_reuse_rejection_reasons": [],
            "duplicate_provider_call_count": 0,
            "response_path": "page_load",
            "schema_version": "v2",
        }
    else:
        force = payload.mode == "force_run_agents"
        job_id, _ = await service.queue_agent_run(
            deposit_amount=payload.deposit_amount,
            sale_proceeds=payload.sale_proceeds,
        )
        background_tasks.add_task(
            run_agent_pipeline,
            user.id,
            job_id,
            payload.deposit_amount if payload.deposit_amount is not None else 900.0,
            payload.sale_proceeds,
            force,
        )
        cert = {
            "mode": payload.mode,
            "run_id": str(job_id),
            "total_cards": 0,
            "action_counts": {},
            "thesis_status_counts": {},
            "narrative_contract_present_count": 0,
            "conflict_count_after_sanitize": 0,
            "buy_cards_with_hold_language_count_after_sanitize": 0,
            "hold_cards_with_buy_language_count_after_sanitize": 0,
            "trim_sell_cards_with_buy_language_count_after_sanitize": 0,
            "attempted_llm_calls": None,
            "successful_llm_calls": None,
            "skipped_fresh_verdicts": None,
            "cache_reuse_candidate_count": None,
            "cache_reuse_accepted_count": None,
            "cache_reuse_rejected_count": None,
            "cache_reuse_rejection_reasons": [],
            "duplicate_provider_call_count": None,
            "response_path": "agent_refresh",
            "schema_version": "v2",
            "force_recompute": force,
            "poll": {"job_status": f"/api/v1/diagnostics/finance-intel/jobs/{job_id}", "insights": f"/api/v1/diagnostics/finance-intel/jobs/{job_id}/insights"},
        }

    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    cache_metrics = {
        "cache_reuse_candidate_count": cert.get("cache_reuse_candidate_count"),
        "cache_reuse_accepted_count": cert.get("cache_reuse_accepted_count"),
        "cache_reuse_rejected_count": cert.get("cache_reuse_rejected_count"),
        "cache_reuse_rejection_reasons": cert.get("cache_reuse_rejection_reasons") or [],
        "skipped_fresh_verdicts": cert.get("skipped_fresh_verdicts") or 0,
    }
    status_label, failure_reasons = _status_for_mode(
        payload.mode,
        int(cert.get("total_cards") or 0),
        cert.get("thesis_status_counts") or {},
        {k: int(cert.get(k) or 0) for k in [
            "conflict_count_after_sanitize",
            "buy_cards_with_hold_language_count_after_sanitize",
            "hold_cards_with_buy_language_count_after_sanitize",
            "trim_sell_cards_with_buy_language_count_after_sanitize",
        ]},
        cache_metrics,
    )
    cert["elapsed_ms"] = elapsed_ms
    cert["status"] = status_label
    cert["failure_reasons"] = failure_reasons
    logger.info("finance_intel_runtime_certification user_id=%s payload=%s", user.id, json.dumps(cert, default=str))
    return cert


@router.get("/jobs/{job_id}", response_model=AgentRunStatus)
async def get_cert_job_status(
    job_id: UUID,
    cert_user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Poll cert job status using diagnostics-only auth."""
    from fastapi import HTTPException

    service = RecommendationService(user_id=cert_user.id)
    try:
        return await service.get_job_status(job_id)
    except HTTPException:
        raise
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Failed to fetch diagnostics cert job status %s: %s — returning safe default", job_id, exc
        )
        return AgentRunStatus(
            id=str(job_id),
            status="unknown",
            current_agent="Status unavailable",
            progress_pct=0,
            tickers=[],
            deposit_amount=0.0,
            sale_proceeds=0.0,
            allocation={},
            summary="Job status temporarily unavailable — please retry.",
            error_message=None,
            started_at=None,
            finished_at=None,
        )


@router.get("/jobs/{job_id}/insights", response_model=list[AgentInsight])
async def get_cert_job_insights(
    job_id: UUID,
    cert_user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Read cert job insights using diagnostics-only auth."""
    service = RecommendationService(user_id=cert_user.id)
    return await service.get_agent_insights(run_id=job_id)


@router.post("/research-workers/validate")
async def validate_research_workers_dark_run(
    payload: ResearchWorkersValidateRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Phase 3.6 — operator-only dark-run validation endpoint.

    Invokes the Phase 3.5 validation harness against real Supabase for a
    capped set of tickers and returns a compact safe summary.

    Required env flags (all must be True):
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_RESEARCH_WORKER_VALIDATION_ENABLED=true
      INTEL_V3_RESEARCH_WORKERS_ENABLED=true
      INTEL_V3_EARNINGS_REVIEWER_ENABLED=true

    Tickers capped to MAX_VALIDATE_TICKERS_PER_REQUEST (3) at this layer.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER returns artifact payloads, facts payloads, raw DB rows, or user secrets.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER writes to intel_v3_snapshots.
    """
    settings = get_settings()

    # ── Phase 3/3.5 flag gates — explicit rejection (not silent disabled summary) ─
    if not settings.intel_v3_research_worker_validation_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_RESEARCH_WORKER_VALIDATION_ENABLED is not enabled",
        )
    if not settings.intel_v3_research_workers_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_RESEARCH_WORKERS_ENABLED is not enabled",
        )
    if not settings.intel_v3_earnings_reviewer_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_EARNINGS_REVIEWER_ENABLED is not enabled",
        )

    db_client = get_supabase_client()

    summary = run_validation(
        tickers=payload.tickers,
        user_id=str(user.id),
        db_client=db_client,
        settings=settings,
        max_tickers=MAX_VALIDATE_TICKERS_PER_REQUEST,
    )

    # Return only the compact safe summary — no payloads, no raw rows, no secrets.
    return {
        "requested_tickers": summary.requested_tickers,
        "normalized_tickers": summary.normalized_tickers,
        "attempted_count": summary.attempted_count,
        "written_count": summary.written_count,
        "skipped_count": summary.skipped_count,
        "failed_count": summary.failed_count,
        "artifact_ids": summary.artifact_ids,
        "safe_for_decision_false_count": summary.safe_for_decision_false_count,
        "unexpected_safe_for_decision_true_count": summary.unexpected_safe_for_decision_true_count,
        "forbidden_payload_violation_count": summary.forbidden_payload_violation_count,
        "visible_snapshot_unchanged": summary.visible_snapshot_unchanged,
        "errors": summary.errors,
        "tables_touched": summary.tables_touched,
    }


@router.post("/research-artifacts/observe")
async def observe_research_artifacts(
    payload: ResearchArtifactsObserveRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Phase 4 — operator-only read-only artifact observability endpoint.

    Returns aggregate counters over recent research artifacts for the
    runtime-cert user. No artifact payloads, source URLs, quotes, facts,
    or raw DB rows are returned.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_RESEARCH_ARTIFACT_OBSERVABILITY_ENABLED=true

    Worker/validation flags (Phase 3/3.5) are NOT required — observability
    is independent and controlled solely by its own flag.

    Guardrails applied at this layer:
      - tickers capped to MAX_OBSERVE_TICKERS_PER_REQUEST (10)
      - lookback_days clamped to [1, 365]
      - max_rows clamped to [1, 1000]
      - tickers normalized uppercase and deduplicated

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER returns artifact payloads, facts payloads, raw DB rows, or user secrets.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER writes to intel_v3_snapshots or any artifact table.
    """
    settings = get_settings()

    if not settings.intel_v3_research_artifact_observability_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_RESEARCH_ARTIFACT_OBSERVABILITY_ENABLED is not enabled",
        )

    # ── Guardrail: normalize/dedupe first, then cap ───────────────────────────
    # Normalize and deduplicate before applying the cap so that e.g. 11 raw
    # tickers that collapse to 8 unique ones are not over-rejected.
    normalized_raw = list(
        dict.fromkeys(t.upper().strip() for t in payload.tickers if t.strip())
    )
    tickers_input = normalized_raw[:MAX_OBSERVE_TICKERS_PER_REQUEST]
    lookback_days = max(MIN_OBSERVE_LOOKBACK_DAYS, min(MAX_OBSERVE_LOOKBACK_DAYS, payload.lookback_days))
    max_rows = max(MIN_OBSERVE_ROWS, min(MAX_OBSERVE_ROWS, payload.max_rows))

    db_client = get_supabase_client()

    obs = summarize_recent_research_artifacts(
        user_id=str(user.id),
        db_client=db_client,
        tickers=tickers_input,
        lookback_days=lookback_days,
        max_rows=max_rows,
        settings=settings,
    )

    # Return only the compact safe aggregate summary — never raw payloads or rows.
    return {
        "observability_enabled": obs.observability_enabled,
        "requested_tickers": obs.requested_tickers,
        "normalized_tickers": obs.normalized_tickers,
        "lookback_days": obs.lookback_days,
        "max_rows": obs.max_rows,
        "artifact_count": obs.artifact_count,
        "by_ticker": obs.by_ticker,
        "by_artifact_type": obs.by_artifact_type,
        "by_skill_pack": obs.by_skill_pack,
        "by_confidence_or_trust_level": obs.by_confidence_or_trust_level,
        "by_freshness_status": obs.by_freshness_status,
        "safe_for_decision_false_count": obs.safe_for_decision_false_count,
        "unexpected_safe_for_decision_true_count": obs.unexpected_safe_for_decision_true_count,
        "forbidden_payload_violation_count": obs.forbidden_payload_violation_count,
        "active_count": obs.active_count,
        "inactive_count": obs.inactive_count,
        "invalidated_count": obs.invalidated_count,
        "expired_count": obs.expired_count,
        "artifacts_with_sources_count": obs.artifacts_with_sources_count,
        "artifacts_without_sources_count": obs.artifacts_without_sources_count,
        "artifacts_with_facts_count": obs.artifacts_with_facts_count,
        "artifacts_without_facts_count": obs.artifacts_without_facts_count,
        "missing_evidence_count": obs.missing_evidence_count,
        "visible_snapshot_unchanged": obs.visible_snapshot_unchanged,
        "errors": obs.errors,
        # Phase 6B: Truth Adapter Readiness aggregates — aggregate counters only.
        "readiness_evaluated_count": obs.readiness_evaluated_count,
        "eligible_for_truth_adapter_count": obs.eligible_for_truth_adapter_count,
        "ineligible_for_truth_adapter_count": obs.ineligible_for_truth_adapter_count,
        "eligible_for_decision_consumption_count": obs.eligible_for_decision_consumption_count,
        "safe_for_decision_db_promotion_blocked_count": obs.safe_for_decision_db_promotion_blocked_count,
        "fail_closed_count": obs.fail_closed_count,
        "by_readiness_reason_code": obs.by_readiness_reason_code,
        "artifacts_with_source_linked_facts_count": obs.artifacts_with_source_linked_facts_count,
        "artifacts_without_source_linked_facts_count": obs.artifacts_without_source_linked_facts_count,
        "phase5_ready_but_decision_blocked_count": obs.phase5_ready_but_decision_blocked_count,
        "readiness_visible_snapshot_unchanged": obs.readiness_visible_snapshot_unchanged,
        # Phase 7A: metric observation counters.
        "artifacts_with_metric_observations_count": obs.artifacts_with_metric_observations_count,
        "metric_observation_fact_count": obs.metric_observation_fact_count,
        # Phase 7C: metric observation mix — aggregate-only, no raw values.
        "by_metric_observation_tag": obs.by_metric_observation_tag,
        "by_metric_observation_unit": obs.by_metric_observation_unit,
        "by_metric_observation_form": obs.by_metric_observation_form,
        "artifacts_with_companyfacts_metric_observations_count": obs.artifacts_with_companyfacts_metric_observations_count,
        # Phase 8A: SEC metric truth adapter dry-run — aggregate-only, no raw values.
        # dry_run_safe_for_decision is always False; visible_snapshot_unchanged always True.
        "sec_metric_truth_adapter_dry_run_enabled": obs.sec_metric_truth_adapter_dry_run_enabled,
        "sec_metric_truth_adapter_dry_run_safe_for_decision": obs.sec_metric_truth_adapter_dry_run_safe_for_decision,
        "sec_metric_truth_adapter_artifacts_evaluated_count": obs.sec_metric_truth_adapter_artifacts_evaluated_count,
        "sec_metric_truth_adapter_source_linked_metric_fact_count": obs.sec_metric_truth_adapter_source_linked_metric_fact_count,
        "sec_metric_truth_adapter_unmapped_metric_fact_count": obs.sec_metric_truth_adapter_unmapped_metric_fact_count,
        "sec_metric_truth_adapter_by_ticker": obs.sec_metric_truth_adapter_by_ticker,
        "sec_metric_truth_adapter_by_bucket": obs.sec_metric_truth_adapter_by_bucket,
        "sec_metric_truth_adapter_by_tag": obs.sec_metric_truth_adapter_by_tag,
        "sec_metric_truth_adapter_by_unit": obs.sec_metric_truth_adapter_by_unit,
        "sec_metric_truth_adapter_by_form": obs.sec_metric_truth_adapter_by_form,
        "sec_metric_truth_adapter_missing_buckets_by_ticker": obs.sec_metric_truth_adapter_missing_buckets_by_ticker,
        "sec_metric_truth_adapter_visible_snapshot_unchanged": obs.sec_metric_truth_adapter_visible_snapshot_unchanged,
        # Phase 8B: SEC metric evidence snapshot dry-run — per-ticker diagnostic contract.
        # snapshot_safe_for_decision is always False; visible_snapshot_unchanged always True.
        # by_ticker is aggregate-only: no raw metric values, no structured_payload, no source URLs.
        "sec_metric_evidence_snapshot_dry_run_enabled": obs.sec_metric_evidence_snapshot_dry_run_enabled,
        "sec_metric_evidence_snapshot_safe_for_decision": obs.sec_metric_evidence_snapshot_safe_for_decision,
        "sec_metric_evidence_snapshot_visible_snapshot_unchanged": obs.sec_metric_evidence_snapshot_visible_snapshot_unchanged,
        "sec_metric_evidence_snapshot_tickers_evaluated_count": obs.sec_metric_evidence_snapshot_tickers_evaluated_count,
        "sec_metric_evidence_snapshot_tickers_with_any_source_linked_evidence_count": obs.sec_metric_evidence_snapshot_tickers_with_any_source_linked_evidence_count,
        "sec_metric_evidence_snapshot_tickers_ready_for_future_adapter_count": obs.sec_metric_evidence_snapshot_tickers_ready_for_future_adapter_count,
        "sec_metric_evidence_snapshot_tickers_blocked_from_decision_count": obs.sec_metric_evidence_snapshot_tickers_blocked_from_decision_count,
        "sec_metric_evidence_snapshot_by_ticker": obs.sec_metric_evidence_snapshot_by_ticker,
    }


@router.post("/sec-metric-evidence/portfolio-coverage")
async def get_portfolio_sec_metric_coverage(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Phase 8D — operator-only portfolio SEC metric evidence coverage diagnostic.

    Compares current portfolio tickers (from positions/portfolio_snapshots) against
    Phase 8B SEC metric evidence output to produce a portfolio-wide coverage summary.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_SEC_METRIC_PORTFOLIO_COVERAGE_DRY_RUN_ENABLED=true

    Worker/validation/observability flags are NOT required — this endpoint is
    independent and controlled solely by INTEL_V3_SEC_METRIC_PORTFOLIO_COVERAGE_DRY_RUN_ENABLED.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER returns raw metric values, structured_payload, source URLs, raw DB rows.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER writes to any DB table.
    NEVER sets safe_for_decision=True.
    """
    settings = get_settings()

    if not settings.intel_v3_sec_metric_portfolio_coverage_dry_run_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_SEC_METRIC_PORTFOLIO_COVERAGE_DRY_RUN_ENABLED is not enabled",
        )

    db_client = get_supabase_client()

    coverage = compute_portfolio_sec_metric_coverage(
        user_id=str(user.id),
        db_client=db_client,
        settings=settings,
    )

    # Return only aggregate-safe fields — no raw payloads, no source URLs, no raw rows.
    return {
        "sec_metric_portfolio_coverage_dry_run_enabled": coverage.coverage_enabled,
        "sec_metric_portfolio_coverage_safe_for_decision": coverage.safe_for_decision,
        "sec_metric_portfolio_coverage_visible_snapshot_unchanged": coverage.visible_snapshot_unchanged,
        "portfolio_ticker_count": coverage.portfolio_ticker_count,
        "portfolio_tickers_evaluated": coverage.portfolio_tickers_evaluated,
        "tickers_with_sec_research_artifacts_count": coverage.tickers_with_sec_research_artifacts_count,
        "tickers_without_sec_research_artifacts_count": coverage.tickers_without_sec_research_artifacts_count,
        "tickers_with_source_linked_metric_evidence_count": coverage.tickers_with_source_linked_metric_evidence_count,
        "tickers_ready_for_future_adapter_count": coverage.tickers_ready_for_future_adapter_count,
        "tickers_partial_for_future_adapter_count": coverage.tickers_partial_for_future_adapter_count,
        "tickers_blocked_for_future_adapter_count": coverage.tickers_blocked_for_future_adapter_count,
        "tickers_without_sec_metric_coverage": coverage.tickers_without_sec_metric_coverage,
        "readiness_counts": coverage.readiness_counts,
        "by_ticker": coverage.by_ticker,
        "errors": coverage.errors,
    }


@router.post("/sec-metric-evidence/expand-coverage")
async def expand_sec_metric_evidence_coverage(
    payload: SecMetricCoverageExpansionRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Phase 8E — operator-only SEC metric evidence coverage expansion.

    Selects eligible SEC-company portfolio tickers missing SEC metric evidence
    and (when dry_run=false) runs the existing Phase 3/7A artifact writer for
    each selected ticker. ETF/Crypto/already-covered tickers are skipped.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_SEC_METRIC_PORTFOLIO_COVERAGE_EXPANSION_ENABLED=true

    Additional flags required for writes (dry_run=false):
      INTEL_V3_RESEARCH_WORKERS_ENABLED=true
      INTEL_V3_EARNINGS_REVIEWER_ENABLED=true

    max_tickers is capped defensively to MAX_TICKERS_PER_EXPANSION (10).
    include_tickers restricts candidates to those tickers only.
    exclude_tickers removes tickers from the candidate set.
    dry_run=true computes candidates but writes nothing.
    dry_run=false calls the existing SEC writer/validation path.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER returns raw metric values, structured_payload, source URLs, raw DB rows.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER writes to intel_v3_snapshots.
    NEVER sets safe_for_decision=True.
    """
    settings = get_settings()

    if not settings.intel_v3_sec_metric_portfolio_coverage_expansion_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_SEC_METRIC_PORTFOLIO_COVERAGE_EXPANSION_ENABLED is not enabled",
        )

    db_client = get_supabase_client()

    result = compute_coverage_expansion(
        user_id=str(user.id),
        db_client=db_client,
        max_tickers=payload.max_tickers,
        include_tickers=payload.include_tickers,
        exclude_tickers=payload.exclude_tickers,
        dry_run=payload.dry_run,
        settings=settings,
    )

    # Return only aggregate-safe fields — no raw payloads, no source URLs, no raw rows.
    return {
        "coverage_expansion_enabled": result.coverage_expansion_enabled,
        "dry_run": result.dry_run,
        "safe_for_decision": result.safe_for_decision,
        "visible_snapshot_unchanged": result.visible_snapshot_unchanged,
        "portfolio_ticker_count": result.portfolio_ticker_count,
        "candidate_count": result.candidate_count,
        "selected_tickers": result.selected_tickers,
        "skipped_tickers_by_reason": result.skipped_tickers_by_reason,
        "attempted_count": result.attempted_count,
        "written_count": result.written_count,
        "skipped_count": result.skipped_count,
        "failed_count": result.failed_count,
        "artifact_ids": result.artifact_ids,
        "safe_for_decision_false_count": result.safe_for_decision_false_count,
        "unexpected_safe_for_decision_true_count": result.unexpected_safe_for_decision_true_count,
        "forbidden_payload_violation_count": result.forbidden_payload_violation_count,
        "before_coverage_summary": result.before_coverage_summary,
        "after_coverage_summary": result.after_coverage_summary,
        "errors": result.errors,
    }


@router.post("/sec-metric-evidence/readiness-adapter")
async def get_sec_metric_evidence_readiness(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Phase 9 — operator-only SEC metric evidence readiness adapter.

    Classifies each portfolio ticker into a typed readiness status
    (READY / PARTIAL / BLOCKED / SKIPPED_NON_COMPANY) based on existing
    Phase 8 SEC metric evidence. Produces aggregate diagnostics for future
    Phase 10 truth-adapter input consumption planning.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_SEC_METRIC_EVIDENCE_READINESS_ADAPTER_ENABLED=true

    This endpoint is shadow/readiness-only. It does NOT:
      - Feed SEC metrics into DecisionInputV3.
      - Change visible Buy/Hold/Trim/Sell decisions.
      - Invoke SEC coverage expansion write mode.
      - Retry blocked tickers (BLSH/KLAR/TSM).

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER returns raw metric values, structured_payload, source URLs, raw DB rows.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER writes to any DB table.
    NEVER sets safe_for_decision=True.
    """
    settings = get_settings()

    if not settings.intel_v3_sec_metric_evidence_readiness_adapter_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_SEC_METRIC_EVIDENCE_READINESS_ADAPTER_ENABLED is not enabled",
        )

    db_client = get_supabase_client()

    readiness = compute_sec_metric_evidence_readiness(
        user_id=str(user.id),
        db_client=db_client,
        settings=settings,
    )

    # Return only aggregate-safe fields — no raw payloads, no source URLs, no raw rows.
    return {
        "sec_metric_evidence_readiness_adapter_enabled": readiness.adapter_enabled,
        "safe_for_decision": readiness.safe_for_decision,
        "visible_snapshot_unchanged": readiness.visible_snapshot_unchanged,
        "portfolio_ticker_count": readiness.portfolio_ticker_count,
        "ready_count": readiness.ready_count,
        "partial_count": readiness.partial_count,
        "blocked_count": readiness.blocked_count,
        "skipped_non_company_count": readiness.skipped_non_company_count,
        "ready_tickers": readiness.ready_tickers,
        "partial_tickers_with_missing_groups": readiness.partial_tickers_with_missing_groups,
        "blocked_tickers_with_reason": readiness.blocked_tickers_with_reason,
        "skipped_tickers_by_reason": readiness.skipped_tickers_by_reason,
        "errors": readiness.errors,
    }


# ── Phase 10 — Evidence Source Registry diagnostics ──────────────────────────

@router.post("/evidence-source-registry")
async def get_evidence_source_registry_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Return the Phase 10 Evidence Source Registry governance summary.

    Protected diagnostics endpoint — ops-only via runtime cert auth.
    Returns aggregate governance metadata only: lane counts, source counts,
    trust tiers, lifecycle statuses. No raw metric values, no payloads,
    no source URLs, no decision signals.

    Governance invariants preserved:
      - safe_for_decision is always False.
      - visible_snapshot_unchanged is always True.
      - No decision path is called or modified.
      - No DB writes, no provider calls, no LLM calls.
      - No visible Buy/Hold/Trim/Sell behavior changes.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER sets safe_for_decision=True.
    """
    settings = get_settings()

    if not settings.intel_v3_evidence_source_registry_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_EVIDENCE_SOURCE_REGISTRY_DIAGNOSTICS_ENABLED is not enabled",
        )

    summary = build_registry_summary()
    # Governance hard-lock: safe_for_decision must remain False.
    summary["safe_for_decision"] = False
    summary["visible_snapshot_unchanged"] = True
    return summary


# ── Phase 11 — SEC Metric Truth Adapter v1 diagnostics ───────────────────────

@router.post("/sec-metric-truth-adapter-v1")
async def get_sec_metric_truth_adapter_v1_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 11 — operator-only SEC Metric Truth Adapter v1 governance diagnostics.

    Returns governance gate status, readiness counts, and expected evidence-quality
    upgrade counts (aggregate only). No raw metric values, no payloads, no source
    URLs. Diagnostics-only — does not run the Intel v3 snapshot or change decisions.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_SEC_METRIC_TRUTH_ADAPTER_V1_DIAGNOSTICS_ENABLED=true

    Governance invariants preserved:
      - safe_for_decision is always False.
      - visible_snapshot_unchanged is always True.
      - No decision path is called or modified.
      - No DB writes, no provider calls, no LLM calls.
      - No Buy/Hold/Trim/Sell behavior changes.
      - No raw metric values, no structured payloads, no source URLs.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER sets safe_for_decision=True.
    """
    settings = get_settings()

    if not settings.intel_v3_sec_metric_truth_adapter_v1_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_SEC_METRIC_TRUTH_ADAPTER_V1_DIAGNOSTICS_ENABLED is not enabled",
        )

    gate_passed, gate_reason = check_governance_gate()

    db_client = get_supabase_client()
    readiness = compute_sec_readiness_for_phase11_adapter(
        user_id=str(user.id),
        db_client=db_client,
    )

    # Compute expected upgrade counts — how many tickers WOULD receive a
    # positive evidence-quality contribution (aggregate only, no raw metrics).
    evidence_quality_upgrades_ready = 0
    evidence_quality_upgrades_partial = 0

    if gate_passed:
        for ticker in readiness.ready_tickers:
            sig = build_sec_fundamentals_signal(ticker=ticker, readiness_result=readiness)
            if sig.evidence_quality_contribution is not None:
                evidence_quality_upgrades_ready += 1
        for ticker in readiness.partial_tickers_with_missing_groups:
            sig = build_sec_fundamentals_signal(ticker=ticker, readiness_result=readiness)
            if sig.evidence_quality_contribution is not None:
                evidence_quality_upgrades_partial += 1

    return {
        "adapter_version": SEC_METRIC_TRUTH_ADAPTER_V1_CONTRACT_VERSION,
        "safe_for_decision": False,
        "visible_snapshot_unchanged": True,
        "governance_gate_passed": gate_passed,
        "governance_gate_reason": gate_reason,
        "consumption_enabled": settings.intel_v3_sec_metric_truth_adapter_v1_enabled,
        "portfolio_ticker_count": readiness.portfolio_ticker_count,
        "ready_count": readiness.ready_count,
        "partial_count": readiness.partial_count,
        "blocked_count": readiness.blocked_count,
        "skipped_non_company_count": readiness.skipped_non_company_count,
        "evidence_quality_upgrades_ready": evidence_quality_upgrades_ready,
        "evidence_quality_upgrades_partial": evidence_quality_upgrades_partial,
        "errors": readiness.errors,
    }


# ── Phase 13 — Valuation Context Adapter v1 diagnostics ─────────────────────

@router.post("/valuation-context-adapter-v1")
async def get_valuation_context_adapter_v1_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 13 — operator-only Valuation Context Adapter v1 governance diagnostics.

    Returns governance gate status and signal-status counts by category
    (aggregate only). No raw valuation values, no metric keys, no payloads,
    no price targets. Diagnostics-only — does not run the Intel v3 snapshot or
    change decisions.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_VALUATION_CONTEXT_ADAPTER_V1_DIAGNOSTICS_ENABLED=true

    Governance invariants preserved:
      - safe_for_decision is always False.
      - visible_snapshot_unchanged is always True.
      - No decision path is called or modified.
      - No DB writes, no provider calls, no LLM calls.
      - No Buy/Hold/Trim/Sell behavior changes.
      - No raw valuation values, no metric keys, no structured payloads.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER sets safe_for_decision=True.
    """
    settings = get_settings()

    if not settings.intel_v3_valuation_context_adapter_v1_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_VALUATION_CONTEXT_ADAPTER_V1_DIAGNOSTICS_ENABLED is not enabled",
        )

    gate_passed, gate_reason = check_valuation_governance_gate()

    db_client = get_supabase_client()
    readiness = compute_sec_readiness_for_phase11_adapter(
        user_id=str(user.id),
        db_client=db_client,
    )

    # Compute readiness status counts (aggregate only, no raw values).
    # Phase 13 is readiness-only — no price_context changes.
    # Use has_market_price=True for diagnostics (counts governance/readiness only).
    status_counts: dict[str, int] = {s.value: 0 for s in ValuationSignalStatus}

    if gate_passed:
        # Company tickers: READY
        for ticker in readiness.ready_tickers:
            sig = build_valuation_context_signal(
                ticker=ticker,
                category="stock",
                sec_readiness=readiness,
                has_market_price=True,
            )
            status_counts[sig.status.value] = status_counts.get(sig.status.value, 0) + 1

        # Company tickers: PARTIAL
        for ticker in readiness.partial_tickers_with_missing_groups:
            sig = build_valuation_context_signal(
                ticker=ticker,
                category="stock",
                sec_readiness=readiness,
                has_market_price=True,
            )
            status_counts[sig.status.value] = status_counts.get(sig.status.value, 0) + 1

        # Non-company tickers: SKIPPED
        for _reason, tickers in readiness.skipped_tickers_by_reason.items():
            for ticker in tickers:
                sig = build_valuation_context_signal(
                    ticker=ticker,
                    category="etf",
                    sec_readiness=readiness,
                    has_market_price=True,
                )
                status_counts[sig.status.value] = status_counts.get(sig.status.value, 0) + 1

        # Blocked tickers
        for ticker in readiness.blocked_tickers_with_reason:
            sig = build_valuation_context_signal(
                ticker=ticker,
                category="stock",
                sec_readiness=readiness,
                has_market_price=True,
            )
            status_counts[sig.status.value] = status_counts.get(sig.status.value, 0) + 1

    return {
        "adapter_version": VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION,
        "safe_for_decision": False,
        "visible_snapshot_unchanged": True,
        "price_context_unchanged": True,
        "readiness_only": True,
        "governance_gate_passed": gate_passed,
        "governance_gate_reason": gate_reason,
        "consumption_enabled": settings.intel_v3_valuation_context_adapter_v1_enabled,
        "portfolio_ticker_count": readiness.portfolio_ticker_count,
        "readiness_status_counts": status_counts,
        "errors": readiness.errors,
    }


# ── Phase 14A — Valuation Data Audit v1 diagnostics ─────────────────────────

@router.post("/valuation-data-audit-v1")
async def get_valuation_data_audit_v1_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 14A — operator-only Valuation Data Audit v1 diagnostics.

    Read-only stored-data audit that reports whether existing SEC fundamentals
    and portfolio data are sufficient to support future valuation ratio
    computation (Phase 14B). Returns aggregate-only counts — no raw metric
    values, no ratios, no PriceBand, no per-ticker raw rows.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_VALUATION_DATA_AUDIT_V1_DIAGNOSTICS_ENABLED=true

    Governance invariants preserved:
      - safe_for_decision is always False.
      - visible_snapshot_unchanged is always True.
      - read_only is always True.
      - diagnostics_only is always True.
      - valuation_ratios_computed is always False.
      - price_context_unchanged is always True.
      - No decision path is called or modified.
      - No DB writes, no provider calls, no LLM calls.
      - No Buy/Hold/Trim/Sell behavior changes.
      - No raw metric values, no valuation ratios, no PriceBand values.
      - TTM blocked: _MAX_PERIODS_PER_TAG=2 < 4 periods needed for TTM.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER sets safe_for_decision=True.
    NEVER computes P/E, P/B, EV/EBITDA, earnings yield, or any valuation ratio.
    NEVER produces PriceBand contributions.
    NEVER modifies DecisionInputV3.
    """
    settings = get_settings()

    if not settings.intel_v3_valuation_data_audit_v1_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_VALUATION_DATA_AUDIT_V1_DIAGNOSTICS_ENABLED is not enabled",
        )

    db_client = get_supabase_client()

    # ── Step 1: Compute Phase 9 SEC metric readiness ──────────────────────────
    readiness = compute_sec_readiness_for_phase11_adapter(
        user_id=str(user.id),
        db_client=db_client,
    )

    # ── Step 2: Read positions for portfolio category info ────────────────────
    # positions.category provides portfolio category (Core/ETF/Crypto/IPO/SELL).
    # This is NOT financial sector — financial sector (Technology/Healthcare/etc.)
    # is stored in intel_v3_snapshots.raw.fundamentals and is not queried here.
    company_ticker_categories: dict[str, str] = {}
    errors: list[str] = []

    try:
        pos_result = (
            db_client.table("positions")
            .select("ticker,category")
            .eq("user_id", str(user.id))
            .execute()
        )
        for row in (pos_result.data or []):
            ticker = str(row.get("ticker") or "").upper().strip()
            category = str(row.get("category") or "")
            if ticker:
                company_ticker_categories[ticker] = category
    except Exception as exc:  # noqa: BLE001
        errors.append(f"positions_category_query_error: {exc}")

    # ── Step 3: Build the aggregate-only audit ────────────────────────────────
    audit = build_valuation_data_audit(
        readiness=readiness,
        company_ticker_categories=company_ticker_categories,
    )

    # Merge any endpoint-layer errors into the response.
    all_errors = list(audit.errors) + errors

    # Return aggregate-only response — no raw values, no ratios, no PriceBand.
    return {
        "adapter_version": VALUATION_DATA_AUDIT_V1_CONTRACT_VERSION,
        "safe_for_decision": False,
        "visible_snapshot_unchanged": True,
        "read_only": True,
        "diagnostics_only": True,
        "valuation_ratios_computed": False,
        "price_context_unchanged": True,
        "portfolio_ticker_count": audit.portfolio_ticker_count,
        "company_ticker_count": audit.company_ticker_count,
        "non_company_ticker_count": audit.non_company_ticker_count,
        "sec_ready_count": audit.sec_ready_count,
        "sec_partial_count": audit.sec_partial_count,
        "sec_blocked_count": audit.sec_blocked_count,
        "latest_fy_eps_available_count": audit.latest_fy_eps_available_count,
        "latest_fy_eps_diluted_available_count": audit.latest_fy_eps_diluted_available_count,
        "stockholders_equity_available_count": audit.stockholders_equity_available_count,
        "market_price_available_count": audit.market_price_available_count,
        "market_price_fresh_count": audit.market_price_fresh_count,
        "market_price_source_note": audit.market_price_source_note,
        "sector_available_count": audit.sector_available_count,
        "sector_missing_count": audit.sector_missing_count,
        "sector_source_note": audit.sector_source_note,
        "eligible_for_future_fy_earnings_yield_count": audit.eligible_for_future_fy_earnings_yield_count,
        "eligible_for_future_book_value_proxy_count": audit.eligible_for_future_book_value_proxy_count,
        "requires_provider_or_coverage_expansion_count": audit.requires_provider_or_coverage_expansion_count,
        "ttm_blocked_by_period_limit": audit.ttm_blocked_by_period_limit,
        "period_limit_per_tag": audit.period_limit_per_tag,
        "errors": all_errors,
    }


# ── Phase 14B — Valuation Input Verification v1 diagnostics ─────────────────

@router.post("/valuation-input-verification-v1")
async def get_valuation_input_verification_v1_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 14B — operator-only Valuation Input Verification v1 diagnostics.

    Verifies actual stored inputs needed for future FY EPS earnings-yield
    computation: raw EPS facts from research_artifact_facts, equity facts,
    stored price availability/freshness from price_history, and financial
    sector availability from stored records. Returns aggregate-only counts.

    Key difference from Phase 14A:
        Phase 14A inferred EPS availability from Phase 9 bucket readiness.
        Phase 14B verifies raw EPS facts from actual stored fact records.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_VALUATION_INPUT_VERIFICATION_V1_DIAGNOSTICS_ENABLED=true

    Governance invariants preserved:
      - safe_for_decision is always False.
      - visible_snapshot_unchanged is always True.
      - read_only is always True.
      - diagnostics_only is always True.
      - valuation_ratios_computed is always False.
      - earnings_yield_computed is always False.
      - price_context_unchanged is always True.
      - No decision path is called or modified.
      - No DB writes, no provider calls, no LLM calls.
      - No Buy/Hold/Trim/Sell behavior changes.
      - No raw metric values, no valuation ratios, no PriceBand values.
      - TTM blocked: _MAX_PERIODS_PER_TAG=2 < 4 periods needed for TTM.
      - Financial sector: not available from stored per-ticker records (gap noted).

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER sets safe_for_decision=True.
    NEVER computes P/E, P/B, EV/EBITDA, earnings yield, or any valuation ratio.
    NEVER produces PriceBand contributions.
    NEVER modifies DecisionInputV3.
    NEVER writes to intel_v3_snapshots or any DB table.
    NEVER calls yfinance, SEC, OpenAI/Anthropic, or any external provider.
    """
    from datetime import datetime, timedelta, timezone

    settings = get_settings()

    if not settings.intel_v3_valuation_input_verification_v1_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_VALUATION_INPUT_VERIFICATION_V1_DIAGNOSTICS_ENABLED is not enabled",
        )

    db_client = get_supabase_client()

    # ── Step 1: Compute Phase 9 SEC metric readiness ──────────────────────────
    readiness = compute_sec_readiness_for_phase11_adapter(
        user_id=str(user.id),
        db_client=db_client,
    )

    # ── Step 2: Identify company tickers from Phase 9 readiness ───────────────
    company_tickers: set[str] = (
        set(readiness.ready_tickers)
        | set(readiness.partial_tickers_with_missing_groups.keys())
        | set(readiness.blocked_tickers_with_reason.keys())
    )

    errors: list[str] = []

    # ── Step 3: Verify raw EPS/equity facts from stored research_artifact_facts ─
    # Query research_artifacts for company tickers, then their facts.
    # This is a direct stored-record check — not a Phase 9 inference.
    eps_basic_tickers: set[str] = set()
    eps_diluted_tickers: set[str] = set()
    equity_tickers: set[str] = set()
    source_linked_eps_tickers: set[str] = set()
    source_linked_equity_tickers: set[str] = set()

    if company_tickers:
        try:
            art_result = (
                db_client.table("research_artifacts")
                .select("id,ticker")
                .eq("user_id", str(user.id))
                .in_("ticker", list(company_tickers))
                .execute()
            )
            artifact_rows = list(art_result.data or [])

            ticker_by_artifact_id: dict[str, str] = {
                str(row["id"]): str(row.get("ticker") or "").upper().strip()
                for row in artifact_rows
                if row.get("id") and row.get("ticker")
            }
            artifact_ids = list(ticker_by_artifact_id.keys())

            if artifact_ids:
                fact_result = (
                    db_client.table("research_artifact_facts")
                    .select("artifact_id,fact_kind,structured_payload,source_id")
                    .eq("user_id", str(user.id))
                    .in_("artifact_id", artifact_ids)
                    .execute()
                )
                for row in (fact_result.data or []):
                    if str(row.get("fact_kind") or "") != "metric_observation":
                        continue
                    sp = row.get("structured_payload")
                    if not isinstance(sp, dict) or sp.get("claim") != "sec_companyfact_observed":
                        continue

                    aid = str(row.get("artifact_id") or "")
                    ticker = ticker_by_artifact_id.get(aid, "")
                    if not ticker:
                        continue

                    tag = str(sp.get("tag") or "")
                    has_source = bool(
                        row.get("source_id") and str(row.get("source_id")).strip()
                    )

                    if tag == "EarningsPerShareBasic":
                        eps_basic_tickers.add(ticker)
                        if has_source:
                            source_linked_eps_tickers.add(ticker)
                    elif tag == "EarningsPerShareDiluted":
                        eps_diluted_tickers.add(ticker)
                        if has_source:
                            source_linked_eps_tickers.add(ticker)
                    elif tag == "StockholdersEquity":
                        equity_tickers.add(ticker)
                        if has_source:
                            source_linked_equity_tickers.add(ticker)

        except Exception as exc:  # noqa: BLE001
            errors.append(f"research_artifact_facts_query_error: {exc}")

    # ── Step 4: Verify stored price availability/freshness from price_history ──
    # price_history is a global ticker table (no user_id filter).
    # Fresh = price_date within last PRICE_STALE_THRESHOLD_DAYS days.
    # Stale = price_date older than threshold. Missing = no record.
    from ..services.intelligence.v3.valuation_input_verification_v1 import (
        PRICE_STALE_THRESHOLD_DAYS,
    )
    fresh_price_tickers: set[str] = set()
    stale_price_tickers: set[str] = set()

    if company_tickers:
        try:
            now_utc = datetime.now(timezone.utc)
            stale_cutoff_date = (
                now_utc - timedelta(days=PRICE_STALE_THRESHOLD_DAYS)
            ).strftime("%Y-%m-%d")

            price_result = (
                db_client.table("price_history")
                .select("ticker,price_date")
                .in_("ticker", list(company_tickers))
                .order("price_date", desc=True)
                .execute()
            )
            latest_date_by_ticker: dict[str, str] = {}
            for row in (price_result.data or []):
                t = str(row.get("ticker") or "").upper().strip()
                d = str(row.get("price_date") or "")[:10]
                if t and d:
                    if t not in latest_date_by_ticker or d > latest_date_by_ticker[t]:
                        latest_date_by_ticker[t] = d

            for ticker, price_date in latest_date_by_ticker.items():
                if price_date >= stale_cutoff_date:
                    fresh_price_tickers.add(ticker)
                else:
                    stale_price_tickers.add(ticker)

        except Exception as exc:  # noqa: BLE001
            errors.append(f"price_history_query_error: {exc}")

    # ── Step 5: Financial sector availability ──────────────────────────────────
    # Financial sector (Technology/Healthcare/etc.) is from yfinance fundamentals.
    # intel_v3_snapshots stores a full snapshot payload blob — not per-ticker sector
    # in a directly queryable form. positions.category is portfolio category only.
    # Report gap: sector is not available from current stored per-ticker records.
    financial_sector_tickers: set[str] = set()

    # ── Step 6: Build aggregate-only verification result ──────────────────────
    result = build_valuation_input_verification(
        readiness=readiness,
        eps_basic_tickers=eps_basic_tickers,
        eps_diluted_tickers=eps_diluted_tickers,
        equity_tickers=equity_tickers,
        source_linked_eps_tickers=source_linked_eps_tickers,
        source_linked_equity_tickers=source_linked_equity_tickers,
        fresh_price_tickers=fresh_price_tickers,
        stale_price_tickers=stale_price_tickers,
        financial_sector_tickers=financial_sector_tickers,
        extra_errors=errors,
    )

    # Return aggregate-only response — no raw values, no ratios, no PriceBand.
    return {
        "adapter_version": VALUATION_INPUT_VERIFICATION_V1_CONTRACT_VERSION,
        "safe_for_decision": False,
        "visible_snapshot_unchanged": True,
        "read_only": True,
        "diagnostics_only": True,
        "valuation_ratios_computed": False,
        "earnings_yield_computed": False,
        "price_context_unchanged": True,
        "portfolio_ticker_count": result.portfolio_ticker_count,
        "company_ticker_count": result.company_ticker_count,
        "non_company_ticker_count": result.non_company_ticker_count,
        "sec_ready_count": result.sec_ready_count,
        "sec_partial_count": result.sec_partial_count,
        "sec_blocked_count": result.sec_blocked_count,
        "raw_eps_fact_available_count": result.raw_eps_fact_available_count,
        "raw_eps_diluted_fact_available_count": result.raw_eps_diluted_fact_available_count,
        "raw_eps_basic_fact_available_count": result.raw_eps_basic_fact_available_count,
        "raw_equity_fact_available_count": result.raw_equity_fact_available_count,
        "source_linked_eps_fact_count": result.source_linked_eps_fact_count,
        "source_linked_equity_fact_count": result.source_linked_equity_fact_count,
        "stored_price_available_count": result.stored_price_available_count,
        "stored_price_fresh_count": result.stored_price_fresh_count,
        "stored_price_stale_count": result.stored_price_stale_count,
        "stored_price_missing_count": result.stored_price_missing_count,
        "stored_price_source": result.stored_price_source,
        "financial_sector_available_count": result.financial_sector_available_count,
        "financial_sector_missing_count": result.financial_sector_missing_count,
        "financial_sector_source": result.financial_sector_source,
        "eligible_for_future_fy_eps_yield_verified_count": result.eligible_for_future_fy_eps_yield_verified_count,
        "partial_or_degraded_input_count": result.partial_or_degraded_input_count,
        "blocked_or_unusable_input_count": result.blocked_or_unusable_input_count,
        "non_company_excluded_count": result.non_company_excluded_count,
        "ttm_blocked_by_period_limit": result.ttm_blocked_by_period_limit,
        "period_limit_per_tag": result.period_limit_per_tag,
        "errors": result.errors,
    }


@router.post("/price-sector-source-resolution-v1")
async def get_price_sector_source_resolution_v1_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 14C-Prep — operator-only Price + Sector Source Resolution v1 diagnostics.

    Ranks candidate stored sources for current price and financial sector, and
    reports a deterministic certification status (CERTIFIED | PARTIAL |
    UNCERTIFIED | MISSING) for each. Returns aggregate-only counts. Used to
    decide whether Phase 14C valuation computation can proceed without a
    provider-backed ingestion PR.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_PRICE_SECTOR_SOURCE_RESOLUTION_V1_DIAGNOSTICS_ENABLED=true

    Governance invariants preserved (hard locks):
      - safe_for_decision is always False.
      - visible_snapshot_unchanged is always True.
      - read_only is always True.
      - diagnostics_only is always True.
      - valuation_ratios_computed is always False.
      - earnings_yield_computed is always False.
      - price_context_unchanged is always True.
      - No decision path is called or modified.
      - No DB writes, no provider calls, no LLM calls.
      - No Buy/Hold/Trim/Sell behavior changes.
      - No raw price values, sector strings, ratios, or PriceBand values.
      - positions.market_value / cost_basis is always REJECTED as a price source.
      - positions.category is always REJECTED as a financial sector source.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER imports or calls decide() / decision_policy_v1.
    NEVER writes to intel_v3_snapshots or any DB table.
    NEVER calls yfinance, SEC, OpenAI/Anthropic, or any external provider.
    """
    from datetime import datetime, timedelta, timezone

    settings = get_settings()
    if not settings.intel_v3_price_sector_source_resolution_v1_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_PRICE_SECTOR_SOURCE_RESOLUTION_V1_DIAGNOSTICS_ENABLED is not enabled",
        )

    db_client = get_supabase_client()
    errors: list[str] = []

    # ── Step 1: Phase 9 SEC metric readiness → company tickers + anchor ───────
    readiness = compute_sec_readiness_for_phase11_adapter(
        user_id=str(user.id),
        db_client=db_client,
    )
    company_tickers: set[str] = (
        set(readiness.ready_tickers)
        | set(readiness.partial_tickers_with_missing_groups.keys())
        | set(readiness.blocked_tickers_with_reason.keys())
    )
    company_ticker_count = len(company_tickers)
    non_company_ticker_count = readiness.skipped_non_company_count
    portfolio_ticker_count = readiness.portfolio_ticker_count
    # Anchor: SEC-fact-ready (READY+PARTIAL) company tickers — the set Phase 14C
    # would compute earnings yield for. BLOCKED tickers cannot be Phase 14C
    # eligible regardless of price/sector availability.
    company_anchor_count = readiness.ready_count + readiness.partial_count

    company_tickers_list = list(company_tickers)
    cutoff_date = (
        datetime.now(timezone.utc) - timedelta(days=PSR_PRICE_STALE_THRESHOLD_DAYS)
    ).strftime("%Y-%m-%d")

    # ── Step 2: price_history candidate ───────────────────────────────────────
    ph_fresh: set[str] = set()
    ph_stale: set[str] = set()
    if company_tickers_list:
        try:
            res = (
                db_client.table("price_history")
                .select("ticker,price_date")
                .in_("ticker", company_tickers_list)
                .order("price_date", desc=True)
                .execute()
            )
            latest: dict[str, str] = {}
            for row in (res.data or []):
                t = str(row.get("ticker") or "").upper().strip()
                d = str(row.get("price_date") or "")[:10]
                if t and d and (t not in latest or d > latest[t]):
                    latest[t] = d
            for t, d in latest.items():
                if t in company_tickers:
                    (ph_fresh if d >= cutoff_date else ph_stale).add(t)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"price_history_query_error: {exc}")

    price_history_cand = PriceCandidateStats(
        name=PRICE_CANDIDATE_PRICE_HISTORY,
        available_count=len(ph_fresh) + len(ph_stale),
        fresh_count=len(ph_fresh),
        stale_count=len(ph_stale),
        missing_count=max(0, company_ticker_count - len(ph_fresh) - len(ph_stale)),
        freshness_basis="price_date",
    )

    # ── Step 3: market_snapshots candidate (price + sector) ───────────────────
    ms_price_fresh: set[str] = set()
    ms_price_stale: set[str] = set()
    ms_sector: set[str] = set()
    ms_industry: set[str] = set()
    if company_tickers_list:
        try:
            res = (
                db_client.table("market_snapshots")
                .select("ticker,as_of,price,sector,industry")
                .eq("user_id", str(user.id))
                .in_("ticker", company_tickers_list)
                .order("as_of", desc=True)
                .execute()
            )
            seen_price: dict[str, str] = {}
            for row in (res.data or []):
                t = str(row.get("ticker") or "").upper().strip()
                if not t or t not in company_tickers:
                    continue
                as_of = str(row.get("as_of") or "")
                # Latest record per ticker (rows are ordered desc).
                if t not in seen_price:
                    seen_price[t] = as_of
                    price_val = row.get("price")
                    if price_val is not None and as_of:
                        as_of_date = as_of[:10]
                        if as_of_date >= cutoff_date:
                            ms_price_fresh.add(t)
                        else:
                            ms_price_stale.add(t)
                    sector_val = str(row.get("sector") or "").strip()
                    industry_val = str(row.get("industry") or "").strip()
                    if sector_val:
                        ms_sector.add(t)
                    if industry_val:
                        ms_industry.add(t)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"market_snapshots_query_error: {exc}")

    market_snapshots_price_cand = PriceCandidateStats(
        name=PRICE_CANDIDATE_MARKET_SNAPSHOTS,
        available_count=len(ms_price_fresh) + len(ms_price_stale),
        fresh_count=len(ms_price_fresh),
        stale_count=len(ms_price_stale),
        missing_count=max(0, company_ticker_count - len(ms_price_fresh) - len(ms_price_stale)),
        freshness_basis="as_of",
    )
    market_snapshots_sector_cand = SectorCandidateStats(
        name=SECTOR_CANDIDATE_MARKET_SNAPSHOTS,
        available_count=len(ms_sector),
        industry_available_count=len(ms_industry),
        missing_count=max(0, company_ticker_count - len(ms_sector)),
    )

    # ── Step 4: agent_features candidate (price + sector) ─────────────────────
    af_price_fresh: set[str] = set()
    af_price_stale: set[str] = set()
    af_sector: set[str] = set()
    af_industry: set[str] = set()
    if company_tickers_list:
        try:
            res = (
                db_client.table("agent_features")
                .select("ticker,as_of,price,sector,industry")
                .eq("user_id", str(user.id))
                .in_("ticker", company_tickers_list)
                .order("as_of", desc=True)
                .execute()
            )
            seen: dict[str, str] = {}
            for row in (res.data or []):
                t = str(row.get("ticker") or "").upper().strip()
                if not t or t not in company_tickers or t in seen:
                    continue
                as_of = str(row.get("as_of") or "")
                seen[t] = as_of
                price_val = row.get("price")
                if price_val is not None and as_of:
                    as_of_date = as_of[:10]
                    if as_of_date >= cutoff_date:
                        af_price_fresh.add(t)
                    else:
                        af_price_stale.add(t)
                sector_val = str(row.get("sector") or "").strip()
                industry_val = str(row.get("industry") or "").strip()
                if sector_val:
                    af_sector.add(t)
                if industry_val:
                    af_industry.add(t)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"agent_features_query_error: {exc}")

    agent_features_price_cand = PriceCandidateStats(
        name=PRICE_CANDIDATE_AGENT_FEATURES,
        available_count=len(af_price_fresh) + len(af_price_stale),
        fresh_count=len(af_price_fresh),
        stale_count=len(af_price_stale),
        missing_count=max(0, company_ticker_count - len(af_price_fresh) - len(af_price_stale)),
        freshness_basis="as_of",
    )
    agent_features_sector_cand = SectorCandidateStats(
        name=SECTOR_CANDIDATE_AGENT_FEATURES,
        available_count=len(af_sector),
        industry_available_count=len(af_industry),
        missing_count=max(0, company_ticker_count - len(af_sector)),
    )

    # ── Step 5: positions-derived price candidate — REJECTED ──────────────────
    # positions.market_value / cost_basis has no quote date / freshness basis.
    # Per task spec: must NOT be treated as fresh/current price.
    positions_price_cand = PriceCandidateStats(
        name=PRICE_CANDIDATE_POSITIONS_DERIVED,
        available_count=0,
        fresh_count=0,
        stale_count=0,
        missing_count=company_ticker_count,
        freshness_basis="none",
        rejected_reason="no_quote_date_position_value_is_not_a_price_source",
    )

    # ── Step 6: intel_v3_snapshots payload — peek-only sector candidate ───────
    # The payload is a JSON blob. We do NOT deserialise it at diagnostics time.
    # Reported as available=0 (UNCERTIFIED → treated as MISSING by the
    # classifier when no rows are observed). Even if rows exist, parsing the
    # blob per-ticker is out of scope for this diagnostics path.
    intel_payload_sector_cand = SectorCandidateStats(
        name=SECTOR_CANDIDATE_INTEL_V3_PAYLOAD_BLOB,
        available_count=0,
        industry_available_count=0,
        missing_count=company_ticker_count,
    )

    # ── Step 7: positions.category — REJECTED as financial sector ────────────
    positions_category_sector_cand = SectorCandidateStats(
        name=SECTOR_CANDIDATE_POSITIONS_CATEGORY,
        available_count=0,
        industry_available_count=0,
        missing_count=company_ticker_count,
        rejected_reason="portfolio_category_not_gics_financial_sector",
    )

    # ── Step 8: Pure source resolution ────────────────────────────────────────
    result = build_price_sector_source_resolution(
        portfolio_ticker_count=portfolio_ticker_count,
        company_ticker_count=company_ticker_count,
        non_company_ticker_count=non_company_ticker_count,
        company_anchor_count=company_anchor_count,
        price_candidates=[
            price_history_cand,
            market_snapshots_price_cand,
            agent_features_price_cand,
            positions_price_cand,
        ],
        sector_candidates=[
            market_snapshots_sector_cand,
            agent_features_sector_cand,
            intel_payload_sector_cand,
            positions_category_sector_cand,
        ],
        extra_errors=errors,
    )

    return {
        "adapter_version": PRICE_SECTOR_SOURCE_RESOLUTION_V1_CONTRACT_VERSION,
        "safe_for_decision": False,
        "visible_snapshot_unchanged": True,
        "read_only": True,
        "diagnostics_only": True,
        "valuation_ratios_computed": False,
        "earnings_yield_computed": False,
        "price_context_unchanged": True,
        "portfolio_ticker_count": result.portfolio_ticker_count,
        "company_ticker_count": result.company_ticker_count,
        "non_company_ticker_count": result.non_company_ticker_count,
        "price_source_candidates_checked": result.price_source_candidates_checked,
        "selected_price_source_name": result.selected_price_source_name,
        "selected_price_source_available_count": result.selected_price_source_available_count,
        "selected_price_source_fresh_count": result.selected_price_source_fresh_count,
        "selected_price_source_stale_count": result.selected_price_source_stale_count,
        "selected_price_source_missing_count": result.selected_price_source_missing_count,
        "selected_price_source_freshness_basis": result.selected_price_source_freshness_basis,
        "price_source_certification_status": result.price_source_certification_status,
        "sector_source_candidates_checked": result.sector_source_candidates_checked,
        "selected_sector_source_name": result.selected_sector_source_name,
        "selected_sector_available_count": result.selected_sector_available_count,
        "selected_industry_available_count": result.selected_industry_available_count,
        "selected_sector_missing_count": result.selected_sector_missing_count,
        "sector_source_certification_status": result.sector_source_certification_status,
        "ready_for_phase14c_computation": result.ready_for_phase14c_computation,
        "phase14c_blocking_reasons": result.phase14c_blocking_reasons,
        "recommended_next_step": result.recommended_next_step,
        "errors": result.errors,
    }


# ── Phase 14C — FY EPS Earnings Yield v1 (shadow-only diagnostics) ──────────
# Source-of-truth labels exposed in the response (aggregate-safe — name the
# table only, never raw values).
_PHASE14C_SEC_EPS_SOURCE: str = "research_artifact_facts"
_PHASE14C_PRICE_SOURCE: str = "market_snapshots_table"
_PHASE14C_SECTOR_SOURCE: str = "market_snapshots_sector"


@router.post("/fy-eps-earnings-yield-v1")
async def get_fy_eps_earnings_yield_v1_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 14C — operator-only FY EPS Earnings Yield v1 shadow diagnostics.

    Computes FY EPS earnings yield (EPS / market price) from source-linked
    stored SEC EPS facts and certified market_snapshots price for company
    tickers. Returns aggregate counts and bucket distribution only — never
    raw EPS, prices, yields, source URLs, or per-ticker rows.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_FY_EPS_EARNINGS_YIELD_V1_DIAGNOSTICS_ENABLED=true

    Governance invariants preserved (hard locks):
      - safe_for_decision is always False.
      - shadow_only is always True.
      - visible_snapshot_unchanged is always True.
      - read_only is always True.
      - diagnostics_only is always True.
      - price_context_unchanged is always True.
      - priceband_produced is always False.
      - decision_input_mutated is always False.
      - visible_decision_changed is always False.
      - ttm_computed is always False (FY only — SEC parser period limit).
      - No decision path is called or modified.
      - No DB writes, no provider calls, no LLM calls.
      - No Buy/Hold/Trim/Sell behavior changes.
      - No raw EPS values, raw prices, raw yields, source URLs, or per-ticker
        rows.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER imports or calls decide() / decision_policy_v1 / DecisionInputV3 /
    PriceBand. NEVER writes to intel_v3_snapshots or any DB table. NEVER
    calls yfinance, SEC, OpenAI/Anthropic, or any external provider.
    """
    from datetime import datetime, timedelta, timezone

    settings = get_settings()
    if not settings.intel_v3_fy_eps_earnings_yield_v1_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_FY_EPS_EARNINGS_YIELD_V1_DIAGNOSTICS_ENABLED is not enabled",
        )

    db_client = get_supabase_client()
    errors: list[str] = []

    # ── Step 1: Phase 9 SEC metric readiness → company tickers ────────────────
    readiness = compute_sec_readiness_for_phase11_adapter(
        user_id=str(user.id),
        db_client=db_client,
    )
    company_tickers: set[str] = (
        set(readiness.ready_tickers)
        | set(readiness.partial_tickers_with_missing_groups.keys())
        | set(readiness.blocked_tickers_with_reason.keys())
    )
    company_tickers_list = list(company_tickers)
    company_ticker_count = len(company_tickers)
    non_company_ticker_count = readiness.skipped_non_company_count
    portfolio_ticker_count = readiness.portfolio_ticker_count

    # ── Step 2: FY EPS facts from research_artifact_facts (source-linked) ─────
    # Pick the most-recent ordering-year FY observation per (ticker, tag).
    # Uses extract_fy_eps_observation_from_payload which supports:
    #   Shape A: fiscal_period=="FY" + fiscal_year present
    #   Shape B: fiscal_period=="FY" + fiscal_year absent → filed year fallback
    #   Shape C: fiscal_period absent + form=="10-K" → FY-equivalent
    fy_diluted_by_ticker: dict[str, tuple[int, float]] = {}
    fy_basic_by_ticker: dict[str, tuple[int, float]] = {}
    eps_source_linked_tickers: set[str] = set()
    eps_payload_shape_checked_count: int = 0
    eps_payload_shape_computable_count: int = 0
    skipped_eps_missing_fiscal_period_count: int = 0
    skipped_eps_missing_fiscal_year_count: int = 0
    skipped_eps_missing_numeric_value_count: int = 0
    skipped_eps_not_source_linked_count: int = 0
    # Phase 14C.2 — FY EPS coverage counters (from stored research_artifact_facts).
    fy_eps_candidate_count: int = 0              # EPS observations that are FY annual
    source_linked_fy_eps_candidate_count: int = 0  # FY annual + has source_id
    non_source_linked_fy_eps_rejected_count: int = 0  # FY annual + no source_id
    tickers_with_fy_eps_stored: set[str] = set()  # tickers with ≥1 FY annual EPS stored

    if company_tickers_list:
        try:
            art_result = (
                db_client.table("research_artifacts")
                .select("id,ticker")
                .eq("user_id", str(user.id))
                .in_("ticker", company_tickers_list)
                .execute()
            )
            ticker_by_artifact_id: dict[str, str] = {
                str(row["id"]): str(row.get("ticker") or "").upper().strip()
                for row in (art_result.data or [])
                if row.get("id") and row.get("ticker")
            }
            artifact_ids = list(ticker_by_artifact_id.keys())
            if artifact_ids:
                fact_result = (
                    db_client.table("research_artifact_facts")
                    .select("artifact_id,fact_kind,structured_payload,source_id")
                    .eq("user_id", str(user.id))
                    .in_("artifact_id", artifact_ids)
                    .execute()
                )
                for row in (fact_result.data or []):
                    if str(row.get("fact_kind") or "") != "metric_observation":
                        continue
                    sp = row.get("structured_payload")
                    if not isinstance(sp, dict) or sp.get("claim") != "sec_companyfact_observed":
                        continue

                    # Only count and process EPS tags; skip all others silently.
                    tag_pre = str(sp.get("tag") or "")
                    if tag_pre not in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
                        continue

                    aid = str(row.get("artifact_id") or "")
                    ticker = ticker_by_artifact_id.get(aid, "")
                    if not ticker or ticker not in company_tickers:
                        continue

                    has_source = bool(
                        row.get("source_id") and str(row.get("source_id")).strip()
                    )
                    eps_payload_shape_checked_count += 1

                    # Phase 14C.2 — classify FY annual before extraction.
                    # Mirrors _is_fy_annual_entry() in sec_companyfacts_parser.py.
                    fp_pre = sp.get("fiscal_period")
                    form_pre = str(sp.get("form") or "").upper().strip()
                    fp_pre_upper = str(fp_pre).strip().upper() if fp_pre is not None else None
                    _is_fy_stored = (
                        fp_pre_upper == "FY" or (fp_pre_upper is None and form_pre == "10-K")
                    )
                    if _is_fy_stored:
                        fy_eps_candidate_count += 1
                        tickers_with_fy_eps_stored.add(ticker)
                        if has_source:
                            source_linked_fy_eps_candidate_count += 1
                        else:
                            non_source_linked_fy_eps_rejected_count += 1

                    extraction = extract_fy_eps_observation_from_payload(
                        sp, has_source=has_source
                    )

                    if extraction.skip_reason == SKIP_NOT_SOURCE_LINKED:
                        skipped_eps_not_source_linked_count += 1
                        continue
                    if extraction.skip_reason == SKIP_MISSING_VALUE:
                        skipped_eps_missing_numeric_value_count += 1
                        continue
                    if extraction.skip_reason == SKIP_NOT_FY:
                        skipped_eps_missing_fiscal_period_count += 1
                        continue
                    if extraction.skip_reason == SKIP_MISSING_YEAR:
                        skipped_eps_missing_fiscal_year_count += 1
                        continue
                    if extraction.skip_reason:
                        continue  # Unknown skip reason — safe fallback

                    eps_payload_shape_computable_count += 1
                    eps_source_linked_tickers.add(ticker)

                    tag = extraction.tag
                    fy = extraction.ordering_year
                    val = extraction.eps_value

                    target = (
                        fy_diluted_by_ticker
                        if tag == "EarningsPerShareDiluted"
                        else fy_basic_by_ticker
                    )
                    cur = target.get(ticker)
                    if cur is None or fy > cur[0]:
                        target[ticker] = (fy, val)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"research_artifact_facts_query_error: {exc}")

    # ── Step 3: Certified price/sector/industry from market_snapshots ─────────
    cutoff_date = (
        datetime.now(timezone.utc) - timedelta(days=PSR_PRICE_STALE_THRESHOLD_DAYS)
    ).strftime("%Y-%m-%d")
    price_by_ticker: dict[str, tuple[float, bool]] = {}
    sector_by_ticker: dict[str, bool] = {}
    industry_by_ticker: dict[str, bool] = {}

    if company_tickers_list:
        try:
            res = (
                db_client.table("market_snapshots")
                .select("ticker,as_of,price,sector,industry")
                .eq("user_id", str(user.id))
                .in_("ticker", company_tickers_list)
                .order("as_of", desc=True)
                .execute()
            )
            seen: set[str] = set()
            for row in (res.data or []):
                t = str(row.get("ticker") or "").upper().strip()
                if not t or t not in company_tickers or t in seen:
                    continue
                seen.add(t)
                as_of = str(row.get("as_of") or "")
                price_val = row.get("price")
                if price_val is not None and as_of:
                    try:
                        p = float(price_val)
                        is_fresh = as_of[:10] >= cutoff_date
                        price_by_ticker[t] = (p, is_fresh)
                    except (TypeError, ValueError):
                        pass
                sector_val = str(row.get("sector") or "").strip()
                industry_val = str(row.get("industry") or "").strip()
                if sector_val:
                    sector_by_ticker[t] = True
                if industry_val:
                    industry_by_ticker[t] = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"market_snapshots_query_error: {exc}")

    # ── Step 4: Build sanitized per-ticker records for the pure module ────────
    records: list[EarningsYieldInputRecord] = []
    for ticker in company_tickers_list:
        diluted = fy_diluted_by_ticker.get(ticker)
        basic = fy_basic_by_ticker.get(ticker)
        price_entry = price_by_ticker.get(ticker)
        records.append(
            EarningsYieldInputRecord(
                ticker=ticker,
                fy_diluted_eps=(diluted[1] if diluted is not None else None),
                fy_basic_eps=(basic[1] if basic is not None else None),
                eps_source_linked=(ticker in eps_source_linked_tickers),
                price=(price_entry[0] if price_entry is not None else None),
                price_fresh=(price_entry[1] if price_entry is not None else False),
                sector_available=sector_by_ticker.get(ticker, False),
                industry_available=industry_by_ticker.get(ticker, False),
            )
        )

    # ── Step 5: Pure computation ──────────────────────────────────────────────
    result = build_fy_eps_earnings_yield(
        portfolio_ticker_count=portfolio_ticker_count,
        company_ticker_count=company_ticker_count,
        non_company_ticker_count=non_company_ticker_count,
        records=records,
        sec_eps_source=_PHASE14C_SEC_EPS_SOURCE,
        price_source=_PHASE14C_PRICE_SOURCE,
        sector_source=_PHASE14C_SECTOR_SOURCE,
        extra_errors=errors,
    )

    # Aggregate-only response — never raw EPS, prices, yields, or per-ticker rows.
    return {
        "adapter_version": result.adapter_version,
        "safe_for_decision": result.safe_for_decision,
        "shadow_only": result.shadow_only,
        "visible_snapshot_unchanged": result.visible_snapshot_unchanged,
        "read_only": result.read_only,
        "diagnostics_only": result.diagnostics_only,
        "price_context_unchanged": result.price_context_unchanged,
        "priceband_produced": result.priceband_produced,
        "decision_input_mutated": result.decision_input_mutated,
        "visible_decision_changed": result.visible_decision_changed,
        "valuation_ratios_computed": result.valuation_ratios_computed,
        "earnings_yield_computed": result.earnings_yield_computed,
        "ttm_computed": result.ttm_computed,
        "fy_only": result.fy_only,
        "sec_eps_source": result.sec_eps_source,
        "price_source": result.price_source,
        "sector_source": result.sector_source,
        "eps_preference_order": result.eps_preference_order,
        "portfolio_ticker_count": result.portfolio_ticker_count,
        "company_ticker_count": result.company_ticker_count,
        "non_company_ticker_count": result.non_company_ticker_count,
        "eligible_input_count": result.eligible_input_count,
        "computed_earnings_yield_count": result.computed_earnings_yield_count,
        "skipped_missing_eps_count": result.skipped_missing_eps_count,
        "skipped_missing_price_count": result.skipped_missing_price_count,
        "skipped_stale_price_count": result.skipped_stale_price_count,
        "skipped_non_positive_price_count": result.skipped_non_positive_price_count,
        "skipped_missing_sector_count": result.skipped_missing_sector_count,
        "skipped_invalid_eps_count": result.skipped_invalid_eps_count,
        "negative_eps_count": result.negative_eps_count,
        "positive_eps_count": result.positive_eps_count,
        "zero_eps_count": result.zero_eps_count,
        "diluted_eps_used_count": result.diluted_eps_used_count,
        "basic_eps_fallback_used_count": result.basic_eps_fallback_used_count,
        "source_linked_eps_used_count": result.source_linked_eps_used_count,
        "fresh_price_used_count": result.fresh_price_used_count,
        "sector_available_count": result.sector_available_count,
        "industry_available_count": result.industry_available_count,
        "earnings_yield_distribution_buckets": result.earnings_yield_distribution_buckets,
        "ready_for_future_priceband_phase": result.ready_for_future_priceband_phase,
        "future_priceband_blocking_reasons": result.future_priceband_blocking_reasons,
        "recommended_next_step": result.recommended_next_step,
        "errors": result.errors,
        # Phase 14C.1 — EPS payload shape diagnostics (aggregate-only).
        "eps_payload_shape_checked_count": eps_payload_shape_checked_count,
        "eps_payload_shape_computable_count": eps_payload_shape_computable_count,
        "skipped_eps_missing_fiscal_period_count": skipped_eps_missing_fiscal_period_count,
        "skipped_eps_missing_fiscal_year_count": skipped_eps_missing_fiscal_year_count,
        "skipped_eps_missing_numeric_value_count": skipped_eps_missing_numeric_value_count,
        "skipped_eps_not_source_linked_count": skipped_eps_not_source_linked_count,
        "eps_extraction_schema_version": EPS_EXTRACTION_SCHEMA_VERSION,
        # Phase 14C.2 — FY EPS coverage selection policy diagnostics (aggregate-only).
        # Derived from currently stored research_artifact_facts; requires backfill
        # to reflect the Phase 14C.2 parser fix for tickers fetched before this PR.
        "eps_tag_candidate_count": eps_payload_shape_checked_count,
        "eps_observation_candidate_count": eps_payload_shape_checked_count,
        "fy_eps_candidate_count": fy_eps_candidate_count,
        "selected_latest_period_observation_count": eps_payload_shape_checked_count,
        "selected_fy_eps_observation_count": fy_eps_candidate_count,
        # fy_eps_added_beyond_generic_limit_count is tracked in the parser at
        # fetch time (CompanyFactsParseResult.fy_eps_added_beyond_generic_limit_count)
        # and is not recoverable from stored facts alone; shown as null here.
        "fy_eps_added_beyond_generic_limit_count": None,
        "duplicate_eps_observation_suppressed_count": 0,
        "tickers_with_fy_eps_available_count": len(tickers_with_fy_eps_stored),
        "tickers_with_fy_eps_selected_count": len(
            set(fy_diluted_by_ticker.keys()) | set(fy_basic_by_ticker.keys())
        ),
        "tickers_missing_fy_eps_after_selection_count": company_ticker_count - len(
            set(fy_diluted_by_ticker.keys()) | set(fy_basic_by_ticker.keys())
        ),
        "source_linked_fy_eps_selected_count": source_linked_fy_eps_candidate_count,
        "non_source_linked_fy_eps_rejected_count": non_source_linked_fy_eps_rejected_count,
        "skipped_non_fy_quarterly_eps_count": skipped_eps_missing_fiscal_period_count,
    }


# ── Phase 14C.3 — Ticker-level FY EPS gap diagnostics ───────────────────────

@router.post("/fy-eps-ticker-gap-v1")
async def get_fy_eps_ticker_gap_v1_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 14C.3 — operator-only ticker-level FY EPS gap diagnostics.

    For each company ticker, returns a compact diagnostic object explaining
    exactly why the ticker does or does not have usable FY EPS for the Phase
    14C earnings yield computation. Each missing ticker is assigned exactly
    one stable gap_reason enum.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_FY_EPS_TICKER_GAP_V1_DIAGNOSTICS_ENABLED=true

    Governance invariants preserved (hard locks):
      - safe_for_decision is always False.
      - shadow_only is always True.
      - read_only is always True.
      - diagnostics_only is always True.
      - No PriceBand produced or modified.
      - No DecisionInputV3 mutation.
      - No Buy/Hold/Trim/Sell behavior changes.
      - No TTM computation or quarterly annualization.
      - No DB writes, no provider calls, no LLM calls.
      - selected_eps_value is surfaced here (cert-gated, operator-only).
        It is NEVER returned to frontend page load paths.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER imports or calls decide() / decision_policy_v1 / DecisionInputV3 /
    PriceBand. NEVER writes to intel_v3_snapshots or any DB table.
    """
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    settings = get_settings()
    if not settings.intel_v3_fy_eps_ticker_gap_v1_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_FY_EPS_TICKER_GAP_V1_DIAGNOSTICS_ENABLED is not enabled",
        )

    db_client = get_supabase_client()
    errors: list[str] = []

    # ── Step 1: Phase 9 SEC metric readiness → company tickers ────────────────
    readiness = compute_sec_readiness_for_phase11_adapter(
        user_id=str(user.id),
        db_client=db_client,
    )
    company_tickers: set[str] = (
        set(readiness.ready_tickers)
        | set(readiness.partial_tickers_with_missing_groups.keys())
        | set(readiness.blocked_tickers_with_reason.keys())
    )
    company_tickers_list = list(company_tickers)

    # ── Step 2: Price / sector presence (from market_snapshots) ───────────────
    cutoff_date = (
        datetime.now(timezone.utc) - timedelta(days=PSR_PRICE_STALE_THRESHOLD_DAYS)
    ).strftime("%Y-%m-%d")
    price_present: dict[str, bool] = {}
    sector_present: dict[str, bool] = {}

    if company_tickers_list:
        try:
            res = (
                db_client.table("market_snapshots")
                .select("ticker,as_of,price,sector")
                .eq("user_id", str(user.id))
                .in_("ticker", company_tickers_list)
                .order("as_of", desc=True)
                .execute()
            )
            seen: set[str] = set()
            for row in (res.data or []):
                t = str(row.get("ticker") or "").upper().strip()
                if not t or t not in company_tickers or t in seen:
                    continue
                seen.add(t)
                pv = row.get("price")
                if pv is not None:
                    try:
                        p = float(pv)
                        is_fresh = str(row.get("as_of") or "")[:10] >= cutoff_date
                        price_present[t] = p > 0 and is_fresh
                    except (TypeError, ValueError):
                        pass
                sv = str(row.get("sector") or "").strip()
                if sv:
                    sector_present[t] = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"market_snapshots_query_error: {exc}")

    # ── Step 3: EPS facts from research_artifacts + research_artifact_facts ────
    # Per-ticker tracking maps.
    ticker_has_artifact: dict[str, bool] = {}
    ticker_has_any_fact: dict[str, bool] = {}
    ticker_eps_payload_count: dict[str, int] = defaultdict(int)
    ticker_fy_eps_payload_count: dict[str, int] = defaultdict(int)
    ticker_source_linked_fy_eps_count: dict[str, int] = defaultdict(int)
    ticker_fy_eps_skip_missing_year: dict[str, int] = defaultdict(int)
    ticker_fy_eps_skip_missing_value: dict[str, int] = defaultdict(int)
    # Final computable FY EPS: dict[ticker] = (ordering_year, value, tag, form, source_id_present)
    ticker_fy_diluted: dict[str, tuple[int, float, str, str, bool]] = {}
    ticker_fy_basic: dict[str, tuple[int, float, str, str, bool]] = {}

    if company_tickers_list:
        try:
            art_result = (
                db_client.table("research_artifacts")
                .select("id,ticker")
                .eq("user_id", str(user.id))
                .in_("ticker", company_tickers_list)
                .execute()
            )
            ticker_by_artifact_id: dict[str, str] = {}
            for row in (art_result.data or []):
                aid = str(row.get("id") or "")
                t = str(row.get("ticker") or "").upper().strip()
                if aid and t and t in company_tickers:
                    ticker_by_artifact_id[aid] = t
                    ticker_has_artifact[t] = True

            artifact_ids = list(ticker_by_artifact_id.keys())
            if artifact_ids:
                fact_result = (
                    db_client.table("research_artifact_facts")
                    .select("artifact_id,fact_kind,structured_payload,source_id")
                    .eq("user_id", str(user.id))
                    .in_("artifact_id", artifact_ids)
                    .execute()
                )
                for row in (fact_result.data or []):
                    aid = str(row.get("artifact_id") or "")
                    ticker = ticker_by_artifact_id.get(aid, "")
                    if not ticker:
                        continue

                    ticker_has_any_fact[ticker] = True

                    if str(row.get("fact_kind") or "") != "metric_observation":
                        continue
                    sp = row.get("structured_payload")
                    if not isinstance(sp, dict) or sp.get("claim") != "sec_companyfact_observed":
                        continue

                    tag_pre = str(sp.get("tag") or "")
                    if tag_pre not in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
                        continue

                    ticker_eps_payload_count[ticker] += 1

                    has_source = bool(
                        row.get("source_id") and str(row.get("source_id")).strip()
                    )

                    # Classify FY annual (mirrors existing endpoint logic).
                    fp_pre = sp.get("fiscal_period")
                    form_pre = str(sp.get("form") or "").upper().strip()
                    fp_upper = str(fp_pre).strip().upper() if fp_pre is not None else None
                    is_fy = fp_upper == "FY" or (fp_upper is None and form_pre == "10-K")

                    if not is_fy:
                        continue

                    ticker_fy_eps_payload_count[ticker] += 1
                    if has_source:
                        ticker_source_linked_fy_eps_count[ticker] += 1

                    extraction = extract_fy_eps_observation_from_payload(
                        sp, has_source=has_source
                    )

                    if extraction.skip_reason == SKIP_MISSING_YEAR:
                        ticker_fy_eps_skip_missing_year[ticker] += 1
                        continue
                    if extraction.skip_reason == SKIP_MISSING_VALUE:
                        ticker_fy_eps_skip_missing_value[ticker] += 1
                        continue
                    if extraction.skip_reason:
                        continue

                    fy = extraction.ordering_year
                    val = extraction.eps_value
                    form_str = str(sp.get("form") or "")
                    tag = extraction.tag

                    target = (
                        ticker_fy_diluted
                        if tag == "EarningsPerShareDiluted"
                        else ticker_fy_basic
                    )
                    cur = target.get(ticker)
                    if cur is None or fy > cur[0]:
                        target[ticker] = (fy, val, tag, form_str, has_source)

        except Exception as exc:  # noqa: BLE001
            errors.append(f"research_artifact_facts_query_error: {exc}")

    # ── Step 4: Build per-ticker gap inputs and classify ──────────────────────
    gap_inputs: list[TickerFyEpsGapInput] = []
    for ticker in company_tickers_list:
        diluted = ticker_fy_diluted.get(ticker)
        basic = ticker_fy_basic.get(ticker)

        # Selected observation: diluted preferred, basic fallback.
        selected = diluted or basic
        sel_tag = selected[2] if selected else None
        sel_val = selected[1] if selected else None
        sel_fy = selected[0] if selected else None
        sel_form = selected[3] if selected else None
        sel_src = selected[4] if selected else False

        gap_inputs.append(TickerFyEpsGapInput(
            ticker=ticker,
            company_classification=COMPANY_CLASS_SEC_COMPANY,
            has_price=price_present.get(ticker, False),
            has_sector=sector_present.get(ticker, False),
            has_any_sec_metric_artifact=ticker_has_artifact.get(ticker, False),
            has_any_fact=ticker_has_any_fact.get(ticker, False),
            eps_payload_count=ticker_eps_payload_count.get(ticker, 0),
            fy_eps_payload_count=ticker_fy_eps_payload_count.get(ticker, 0),
            source_linked_fy_eps_count=ticker_source_linked_fy_eps_count.get(ticker, 0),
            fy_eps_skip_missing_year_count=ticker_fy_eps_skip_missing_year.get(ticker, 0),
            fy_eps_skip_missing_value_count=ticker_fy_eps_skip_missing_value.get(ticker, 0),
            has_computable_diluted_fy_eps=diluted is not None,
            has_computable_basic_fy_eps=basic is not None,
            selected_eps_tag=sel_tag,
            selected_eps_value=sel_val,
            selected_eps_fiscal_year=sel_fy,
            selected_eps_form=sel_form,
            selected_eps_source_id_present=sel_src,
        ))

    # ── Step 5: Pure classifier ───────────────────────────────────────────────
    gap_result = build_ticker_fy_eps_gap_diagnostics(
        inputs=gap_inputs,
        extra_errors=errors,
    )

    # ── Step 6: Serialize per-ticker diagnostics (cert-gated response) ────────
    ticker_gap_list = [
        {
            "ticker": d.ticker,
            "company_classification": d.company_classification,
            "has_price": d.has_price,
            "has_sector": d.has_sector,
            "has_any_sec_metric_artifact": d.has_any_sec_metric_artifact,
            "has_any_eps_payload": d.has_any_eps_payload,
            "eps_payload_count": d.eps_payload_count,
            "has_fy_eps_payload": d.has_fy_eps_payload,
            "fy_eps_payload_count": d.fy_eps_payload_count,
            "has_source_linked_fy_eps": d.has_source_linked_fy_eps,
            "usable_fy_eps_for_yield": d.usable_fy_eps_for_yield,
            "selected_eps_tag": d.selected_eps_tag,
            "selected_eps_value": d.selected_eps_value,
            "selected_eps_fiscal_year": d.selected_eps_fiscal_year,
            "selected_eps_form": d.selected_eps_form,
            "selected_eps_source_id_present": d.selected_eps_source_id_present,
            "gap_reason": d.gap_reason,
        }
        for d in gap_result.ticker_gap_diagnostics
    ]

    return {
        "classifier_version": gap_result.classifier_version,
        "safe_for_decision": False,
        "shadow_only": True,
        "read_only": True,
        "diagnostics_only": True,
        "priceband_produced": False,
        "decision_input_mutated": False,
        "visible_decision_changed": False,
        "ttm_computed": False,
        "fy_only": True,
        "ticker_gap_diagnostics_count": gap_result.ticker_gap_diagnostics_count,
        "usable_fy_eps_ticker_count": gap_result.usable_fy_eps_ticker_count,
        "missing_fy_eps_ticker_count": gap_result.missing_fy_eps_ticker_count,
        "unsupported_or_excludable_ticker_count": gap_result.unsupported_or_excludable_ticker_count,
        "potentially_fixable_ticker_count": gap_result.potentially_fixable_ticker_count,
        "gap_reason_counts": gap_result.gap_reason_counts,
        "ticker_gap_diagnostics": ticker_gap_list,
        "errors": gap_result.errors,
    }


@router.post("/sec-fy-eps-coverage-backfill-v1")
async def sec_fy_eps_coverage_backfill_v1(
    request: SecFyEpsBackfillRequest,
    secret_header: str | None = Header(None, alias="X-Finance-Runtime-Cert-Secret"),
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 14C.2 — operator-only SEC FY EPS coverage backfill.

    Re-runs the SEC earnings reviewer for an explicit list of tickers so that
    research_artifact_facts is regenerated with the Phase 14C.2 FY EPS coverage
    policy (latest annual FY EPS retained even when generic latest-N slots are
    filled by quarterly observations).

    This endpoint is safe, explicit, and auditable:
      - dry_run=True (default): returns what would be re-run without any writes.
      - dry_run=False: calls run_earnings_reviewer_dark for each ticker. Each
        call produces a new artifact with the updated metric digest (because FY
        EPS is now included), which the writer stores alongside prior artifacts.
      - Max {_MAX_FY_EPS_BACKFILL_TICKERS} tickers per request.
      - Requires X-Finance-Runtime-Cert-Secret header (same guard as all cert
        endpoints).
      - Requires INTEL_V3_SEC_FY_EPS_BACKFILL_ENABLED=true env flag.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_SEC_FY_EPS_BACKFILL_ENABLED=true

    Governance invariants:
      - safe_for_decision is always False.
      - Does NOT modify DecisionInputV3 or visible Intel v3 decisions.
      - Does NOT produce PriceBand.
      - Does NOT change frontend or Buy/Hold/Trim/Sell behavior.
      - No LLM calls. SEC calls only when SEC flags are enabled (same as
        normal earnings reviewer runner).
      - Dry-run by default — cannot accidentally run in normal app flows.

    NEVER called by frontend page load. NEVER called automatically.
    NEVER writes to intel_v3_snapshots or decision tables.
    """
    _ensure_cert_enabled(secret_header)

    settings = get_settings()
    if not settings.intel_v3_sec_fy_eps_backfill_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_SEC_FY_EPS_BACKFILL_ENABLED is not enabled",
        )

    raw_tickers = [t.upper().strip() for t in (request.tickers or []) if t.strip()]
    if not raw_tickers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="tickers list must be non-empty",
        )
    if len(raw_tickers) > _MAX_FY_EPS_BACKFILL_TICKERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"tickers list exceeds max {_MAX_FY_EPS_BACKFILL_TICKERS} "
                f"tickers per request (got {len(raw_tickers)})"
            ),
        )

    from ..services.intelligence.research_workers.runner import run_earnings_reviewer_dark

    db_client = get_supabase_client()
    results: list[dict] = []

    for ticker in raw_tickers:
        if request.dry_run:
            results.append({
                "ticker": ticker,
                "action": "dry_run_would_rerun",
                "artifact_id": None,
            })
        else:
            try:
                artifact_id = run_earnings_reviewer_dark(
                    user_id=str(user.id),
                    ticker=ticker,
                    db_client=db_client,
                    settings=settings,
                )
                results.append({
                    "ticker": ticker,
                    "action": "rerun_complete",
                    "artifact_id": artifact_id,
                })
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "ticker": ticker,
                    "action": "rerun_error",
                    "artifact_id": None,
                    "error": str(exc),
                })

    rerun_count = sum(1 for r in results if r["action"] == "rerun_complete")
    error_count = sum(1 for r in results if r["action"] == "rerun_error")

    return {
        "safe_for_decision": False,
        "shadow_only": True,
        "read_only": request.dry_run,
        "dry_run": request.dry_run,
        "tickers_requested": raw_tickers,
        "ticker_count": len(raw_tickers),
        "rerun_count": rerun_count,
        "dry_run_count": len(raw_tickers) if request.dry_run else 0,
        "error_count": error_count,
        "results": results,
        "next_step": (
            "Re-run POST /diagnostics/finance-intel/fy-eps-earnings-yield-v1 to "
            "verify computed_earnings_yield_count improved."
            if not request.dry_run
            else "Set dry_run=false to apply the backfill."
        ),
    }


# ── Phase 14C.4 — FY EPS Raw Trace Diagnostic ────────────────────────────────

# EPS tag names expected in SEC us-gaap companyfacts (mirrors parser allowlist).
_RAW_TRACE_EPS_TAGS: frozenset[str] = frozenset({
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",
})
_RAW_TRACE_EPS_CORRECT_UNIT: str = "USD/shares"
_RAW_TRACE_ALLOWED_FORMS: frozenset[str] = frozenset({"10-K", "10-Q"})


def _raw_trace_is_fy_annual(entry: dict) -> bool:
    """Return True if an SEC XBRL entry is a fiscal-year annual observation."""
    fp_raw = entry.get("fp")
    fp = str(fp_raw).strip().upper() if fp_raw is not None else None
    form = str(entry.get("form") or "").upper().strip()
    return fp == "FY" or (fp is None and form == "10-K")


def _fetch_raw_companyfacts_trace(
    ticker: str,
    user_agent: str,
    stored_10k_accessions: frozenset[str],
) -> dict:
    """Fetch raw SEC companyfacts for one ticker and return trace counts.

    Makes at most 2 HTTP requests: CIK lookup + companyfacts JSON.
    Parses companyfacts with empty frozenset to count raw unfiltered FY EPS.
    Also counts how many raw FY EPS entries would be filtered by the stored
    source_accession set.

    Returns a dict of trace fields — never raises. All counts are integers.
    No raw payloads, no accession numbers, no source URLs in the return value.
    """
    _COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    _COMPANYFACTS_TMPL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    _empty: dict = {
        "raw_companyfacts_fetch_attempted": True,
        "raw_companyfacts_fetch_status": "failed",
        "raw_eps_tag_present_count": 0,
        "raw_eps_unit_keys": [],
        "raw_eps_observation_count": 0,
        "raw_fy_eps_observation_count": 0,
        "raw_latest_fy_eps_filed": None,
        "raw_latest_fy_eps_form": None,
        "raw_latest_fy_eps_fp": None,
        "raw_latest_fy_eps_has_accn": False,
        "fy_eps_filtered_by_unit_count": 0,
        "fy_eps_filtered_by_source_accession_count": 0,
        "fy_eps_selected_by_parser_count": 0,
    }

    try:
        import httpx  # deferred import — not needed in test paths
    except ImportError:
        return {**_empty, "raw_companyfacts_fetch_status": "failed"}

    timeout = 12.0
    headers = {"User-Agent": user_agent}
    ticker_upper = ticker.upper().strip()

    try:
        with httpx.Client(headers=headers, timeout=timeout) as client:
            # Request 1: CIK lookup
            r1 = client.get(_COMPANY_TICKERS_URL)
            r1.raise_for_status()
            cik_map: dict = r1.json() or {}
            cik_padded: str | None = None
            for _entry in cik_map.values():
                if (
                    isinstance(_entry, dict)
                    and str(_entry.get("ticker") or "").upper() == ticker_upper
                ):
                    cik_int = _entry.get("cik_str") or _entry.get("cik")
                    if cik_int is not None:
                        cik_padded = str(int(cik_int)).zfill(10)
                    break

            if not cik_padded:
                return {**_empty, "raw_companyfacts_fetch_status": "no_cik"}

            # Request 2: companyfacts JSON (raw, unfiltered)
            cf_url = _COMPANYFACTS_TMPL.format(cik=cik_padded)
            r2 = client.get(cf_url)
            r2.raise_for_status()
            cf_raw: dict = r2.json() or {}

    except Exception as exc:  # noqa: BLE001
        logger.warning("fy_eps_raw_trace_sec_fetch_failed ticker=%s error=%s", ticker_upper, exc)
        return {**_empty, "raw_companyfacts_fetch_status": "failed"}

    # ── Parse raw companyfacts for EPS counts (unfiltered by source accessions) ──
    us_gaap = (cf_raw.get("facts") or {}).get("us-gaap") or {}

    raw_eps_tag_present_count = 0
    raw_eps_unit_keys: list[str] = []
    raw_eps_observation_count = 0
    raw_fy_eps_entries: list[dict] = []

    for tag in sorted(_RAW_TRACE_EPS_TAGS):
        tag_data = us_gaap.get(tag)
        if not isinstance(tag_data, dict):
            continue
        raw_eps_tag_present_count += 1
        units_data = tag_data.get("units") or {}
        for unit_key, entries in units_data.items():
            if unit_key not in raw_eps_unit_keys:
                raw_eps_unit_keys.append(unit_key)
            if unit_key != _RAW_TRACE_EPS_CORRECT_UNIT or not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                form = str(entry.get("form") or "").upper().strip()
                if form not in _RAW_TRACE_ALLOWED_FORMS:
                    continue
                val = entry.get("val")
                if val is None or not isinstance(val, (int, float)):
                    continue
                raw_eps_observation_count += 1
                if _raw_trace_is_fy_annual(entry):
                    raw_fy_eps_entries.append(entry)

    # Sort FY entries by filed date desc to find latest.
    raw_fy_eps_entries.sort(key=lambda e: str(e.get("filed") or ""), reverse=True)
    raw_fy_eps_observation_count = len(raw_fy_eps_entries)

    raw_latest_fy_eps_filed: str | None = None
    raw_latest_fy_eps_form: str | None = None
    raw_latest_fy_eps_fp: str | None = None
    raw_latest_fy_eps_has_accn = False
    if raw_fy_eps_entries:
        latest = raw_fy_eps_entries[0]
        raw_latest_fy_eps_filed = str(latest.get("filed") or "") or None
        raw_latest_fy_eps_form = str(latest.get("form") or "") or None
        raw_latest_fy_eps_fp = str(latest.get("fp") or "") if latest.get("fp") else None
        raw_latest_fy_eps_has_accn = bool(str(latest.get("accn") or "").strip())

    # ── Simulate parser: filter FY EPS by stored 10-K source_accessions ───────
    fy_eps_filtered_by_source_accession_count = 0
    fy_eps_selected_by_parser_count = 0
    for entry in raw_fy_eps_entries:
        accn = str(entry.get("accn") or "").strip()
        if not accn or accn not in stored_10k_accessions:
            fy_eps_filtered_by_source_accession_count += 1
        else:
            fy_eps_selected_by_parser_count += 1

    return {
        "raw_companyfacts_fetch_attempted": True,
        "raw_companyfacts_fetch_status": "success",
        "raw_eps_tag_present_count": raw_eps_tag_present_count,
        "raw_eps_unit_keys": sorted(raw_eps_unit_keys),
        "raw_eps_observation_count": raw_eps_observation_count,
        "raw_fy_eps_observation_count": raw_fy_eps_observation_count,
        "raw_latest_fy_eps_filed": raw_latest_fy_eps_filed,
        "raw_latest_fy_eps_form": raw_latest_fy_eps_form,
        "raw_latest_fy_eps_fp": raw_latest_fy_eps_fp,
        "raw_latest_fy_eps_has_accn": raw_latest_fy_eps_has_accn,
        "fy_eps_filtered_by_unit_count": 0,  # wrong-unit entries excluded before counting
        "fy_eps_filtered_by_source_accession_count": fy_eps_filtered_by_source_accession_count,
        "fy_eps_selected_by_parser_count": fy_eps_selected_by_parser_count,
    }


@router.post("/fy-eps-raw-trace-v1")
async def get_fy_eps_raw_trace_v1_diagnostics(
    request: FyEpsRawTraceRequest,
    secret_header: str | None = Header(None, alias="X-Finance-Runtime-Cert-Secret"),
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 14C.4 — operator-only FY EPS raw trace diagnostic.

    For each explicitly requested ticker (max 5), traces exactly where in the
    data pipeline annual FY EPS is lost: from raw SEC EDGAR companyfacts through
    source accession linkage, parser selection, artifact write, and the Phase
    14C earnings-yield extractor.

    This endpoint answers the production questions from Phase 14C.3 analysis:
    - For AAPL/MSFT/GOOGL/COST/QCOM/ALK: does raw SEC have FY EPS? Is 10-K
      in the stored source_accession set?
    - For BRK-B: are EPS tags absent from raw companyfacts?
    - For BLSH/KLAR/TSM: genuine no-facts cases vs artifact-writer gaps?

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_FY_EPS_RAW_TRACE_V1_DIAGNOSTICS_ENABLED=true

    Optional env for raw SEC fetch:
      SEC_EDGAR_USER_AGENT=<app_name/version contact@email> (required by SEC TOS)
      When absent, raw_companyfacts_fetch_status="no_user_agent" and
      include_raw_counts_only analysis is skipped.

    Governance invariants (non-negotiable hard locks):
      - safe_for_decision is always False.
      - shadow_only is always True.
      - read_only is always True.
      - diagnostics_only is always True.
      - No PriceBand produced.
      - No DecisionInputV3 mutation.
      - No Buy/Hold/Trim/Sell behavior changes.
      - No TTM computation or quarterly annualization.
      - No DB writes.
      - No raw SEC JSON, no source URLs, no unrestricted DB rows in response.
      - Provider calls only within this endpoint (cert-gated, read-only).
      - Max 5 tickers per request.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot reads.
    NEVER imports or calls decide() / decision_policy_v1 / DecisionInputV3 /
    PriceBand. NEVER writes to intel_v3_snapshots or any DB table.
    """
    from collections import defaultdict

    _ensure_cert_enabled(secret_header)

    settings = get_settings()
    if not settings.intel_v3_fy_eps_raw_trace_v1_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_FY_EPS_RAW_TRACE_V1_DIAGNOSTICS_ENABLED is not enabled",
        )

    raw_tickers = [t.upper().strip() for t in (request.tickers or []) if t.strip()]
    if not raw_tickers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="tickers list must be non-empty",
        )
    if len(raw_tickers) > _MAX_FY_EPS_RAW_TRACE_TICKERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"tickers list exceeds max {_MAX_FY_EPS_RAW_TRACE_TICKERS} "
                f"tickers per request (got {len(raw_tickers)})"
            ),
        )

    db_client = get_supabase_client()
    errors: list[str] = []

    # ── Per-ticker data containers ────────────────────────────────────────────
    ticker_artifact_count: dict[str, int] = defaultdict(int)
    ticker_latest_artifact_id: dict[str, str] = {}
    ticker_latest_artifact_created_at: dict[str, str] = {}
    # artifact_id → ticker, created_at
    artifact_id_to_ticker: dict[str, str] = {}
    artifact_id_to_created_at: dict[str, str] = {}

    ticker_fact_count: dict[str, int] = defaultdict(int)
    ticker_eps_fact_count: dict[str, int] = defaultdict(int)
    ticker_fy_eps_fact_count: dict[str, int] = defaultdict(int)
    ticker_quarterly_eps_fact_count: dict[str, int] = defaultdict(int)
    ticker_source_record_count: dict[str, int] = defaultdict(int)
    ticker_source_10k_count: dict[str, int] = defaultdict(int)
    ticker_source_10q_count: dict[str, int] = defaultdict(int)

    # {ticker: {artifact_id: bool}} — which artifacts have FY EPS stored
    ticker_artifact_fy_eps_present: dict[str, dict[str, bool]] = defaultdict(dict)

    # {ticker: count} — extractable FY EPS from stored facts
    ticker_extractor_usable_count: dict[str, int] = defaultdict(int)

    # Stored 10-K accession numbers per ticker (for SEC filter simulation)
    ticker_stored_10k_accessions: dict[str, set[str]] = defaultdict(set)

    # ── Step 1: Fetch research_artifacts for requested tickers ─────────────────
    try:
        art_result = (
            db_client.table("research_artifacts")
            .select("id,ticker,created_at")
            .eq("user_id", str(user.id))
            .in_("ticker", raw_tickers)
            .order("created_at", desc=False)
            .execute()
        )
        for row in (art_result.data or []):
            aid = str(row.get("id") or "")
            t = str(row.get("ticker") or "").upper().strip()
            cat = str(row.get("created_at") or "")
            if not aid or not t or t not in raw_tickers:
                continue
            artifact_id_to_ticker[aid] = t
            artifact_id_to_created_at[aid] = cat
            ticker_artifact_count[t] += 1
            # Track latest artifact by created_at (rows ordered asc, so last wins)
            ticker_latest_artifact_id[t] = aid
            ticker_latest_artifact_created_at[t] = cat
    except Exception as exc:  # noqa: BLE001
        errors.append(f"research_artifacts_query_error: {exc}")

    all_artifact_ids = list(artifact_id_to_ticker.keys())

    # ── Step 2: Fetch research_artifact_facts for all artifact IDs ─────────────
    if all_artifact_ids:
        try:
            fact_result = (
                db_client.table("research_artifact_facts")
                .select("artifact_id,fact_kind,structured_payload,source_id")
                .eq("user_id", str(user.id))
                .in_("artifact_id", all_artifact_ids)
                .execute()
            )
            for row in (fact_result.data or []):
                aid = str(row.get("artifact_id") or "")
                ticker = artifact_id_to_ticker.get(aid)
                if not ticker:
                    continue

                fact_kind = str(row.get("fact_kind") or "")
                sp = row.get("structured_payload") or {}

                ticker_fact_count[ticker] += 1

                # ── sourced_claim facts → source filing form_type analysis ────
                if fact_kind == "sourced_claim" and isinstance(sp, dict):
                    form_type = str(sp.get("form_type") or "").upper().strip()
                    accn = str(sp.get("accession_number") or "").strip()
                    ticker_source_record_count[ticker] += 1
                    if form_type == "10-K":
                        ticker_source_10k_count[ticker] += 1
                        if accn:
                            ticker_stored_10k_accessions[ticker].add(accn)
                    elif form_type == "10-Q":
                        ticker_source_10q_count[ticker] += 1
                    continue

                # ── metric_observation facts → EPS analysis ────────────────────
                if fact_kind != "metric_observation":
                    continue
                if not isinstance(sp, dict) or sp.get("claim") != "sec_companyfact_observed":
                    continue

                tag = str(sp.get("tag") or "")
                if tag not in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
                    continue

                ticker_eps_fact_count[ticker] += 1
                has_source = bool(
                    row.get("source_id") and str(row.get("source_id")).strip()
                )

                # Classify FY vs quarterly (mirrors parser / earnings yield router)
                fp_raw = sp.get("fiscal_period")
                form_raw = str(sp.get("form") or "").upper().strip()
                fp_upper = str(fp_raw).strip().upper() if fp_raw is not None else None
                is_fy = fp_upper == "FY" or (fp_upper is None and form_raw == "10-K")

                if is_fy:
                    ticker_fy_eps_fact_count[ticker] += 1
                    ticker_artifact_fy_eps_present[ticker][aid] = True

                    # Try extraction via Phase 14C extractor
                    extraction = extract_fy_eps_observation_from_payload(
                        sp, has_source=has_source
                    )
                    if not extraction.skip_reason:
                        ticker_extractor_usable_count[ticker] += 1
                else:
                    ticker_quarterly_eps_fact_count[ticker] += 1

        except Exception as exc:  # noqa: BLE001
            errors.append(f"research_artifact_facts_query_error: {exc}")

    # ── Step 3: Build per-ticker trace inputs ──────────────────────────────────
    trace_inputs: list[FyEpsRawTraceInput] = []

    for ticker in raw_tickers:
        has_artifact = ticker_artifact_count.get(ticker, 0) > 0
        latest_aid = ticker_latest_artifact_id.get(ticker)
        stored_10k_accns = frozenset(ticker_stored_10k_accessions.get(ticker, set()))

        # Multi-artifact FY EPS analysis
        artifact_fy_map = ticker_artifact_fy_eps_present.get(ticker, {})
        any_artifact_has_fy_eps = len(artifact_fy_map) > 0
        latest_artifact_has_fy_eps = (
            latest_aid is not None and artifact_fy_map.get(latest_aid, False)
        )

        # ── Optional raw SEC companyfacts fetch ───────────────────────────────
        raw_trace_fields: dict = {
            "raw_companyfacts_fetch_attempted": False,
            "raw_companyfacts_fetch_status": "skipped",
            "raw_eps_tag_present_count": 0,
            "raw_eps_unit_keys": [],
            "raw_eps_observation_count": 0,
            "raw_fy_eps_observation_count": 0,
            "raw_latest_fy_eps_filed": None,
            "raw_latest_fy_eps_form": None,
            "raw_latest_fy_eps_fp": None,
            "raw_latest_fy_eps_has_accn": False,
            "fy_eps_filtered_by_unit_count": 0,
            "fy_eps_filtered_by_source_accession_count": 0,
            "fy_eps_selected_by_parser_count": 0,
        }

        if request.include_raw_counts_only:
            ua = (settings.sec_edgar_user_agent or "").strip()
            if not ua:
                raw_trace_fields["raw_companyfacts_fetch_attempted"] = True
                raw_trace_fields["raw_companyfacts_fetch_status"] = "no_user_agent"
            else:
                try:
                    raw_trace_fields = _fetch_raw_companyfacts_trace(
                        ticker=ticker,
                        user_agent=ua,
                        stored_10k_accessions=stored_10k_accns,
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"raw_sec_fetch_error ticker={ticker}: {exc}")
                    raw_trace_fields["raw_companyfacts_fetch_attempted"] = True
                    raw_trace_fields["raw_companyfacts_fetch_status"] = "failed"

        inp = FyEpsRawTraceInput(
            ticker=ticker,
            has_research_artifact=has_artifact,
            artifact_count=ticker_artifact_count.get(ticker, 0),
            latest_artifact_id=latest_aid,
            artifact_fact_count=ticker_fact_count.get(ticker, 0),
            stored_eps_fact_count=ticker_eps_fact_count.get(ticker, 0),
            stored_fy_eps_fact_count=ticker_fy_eps_fact_count.get(ticker, 0),
            stored_quarterly_eps_fact_count=ticker_quarterly_eps_fact_count.get(ticker, 0),
            source_record_count=ticker_source_record_count.get(ticker, 0),
            source_10k_accession_count=ticker_source_10k_count.get(ticker, 0),
            source_10q_accession_count=ticker_source_10q_count.get(ticker, 0),
            source_accessions_include_10k=ticker_source_10k_count.get(ticker, 0) > 0,
            latest_artifact_has_fy_eps=latest_artifact_has_fy_eps,
            any_artifact_has_fy_eps=any_artifact_has_fy_eps,
            fy_eps_extractor_usable_count=ticker_extractor_usable_count.get(ticker, 0),
            raw_companyfacts_fetch_attempted=raw_trace_fields["raw_companyfacts_fetch_attempted"],
            raw_companyfacts_fetch_status=raw_trace_fields["raw_companyfacts_fetch_status"],
            raw_eps_tag_present_count=raw_trace_fields["raw_eps_tag_present_count"],
            raw_eps_unit_keys=raw_trace_fields["raw_eps_unit_keys"],
            raw_eps_observation_count=raw_trace_fields["raw_eps_observation_count"],
            raw_fy_eps_observation_count=raw_trace_fields["raw_fy_eps_observation_count"],
            raw_latest_fy_eps_filed=raw_trace_fields["raw_latest_fy_eps_filed"],
            raw_latest_fy_eps_form=raw_trace_fields["raw_latest_fy_eps_form"],
            raw_latest_fy_eps_fp=raw_trace_fields["raw_latest_fy_eps_fp"],
            raw_latest_fy_eps_has_accn=raw_trace_fields["raw_latest_fy_eps_has_accn"],
            fy_eps_filtered_by_unit_count=raw_trace_fields["fy_eps_filtered_by_unit_count"],
            fy_eps_filtered_by_source_accession_count=raw_trace_fields[
                "fy_eps_filtered_by_source_accession_count"
            ],
            fy_eps_selected_by_parser_count=raw_trace_fields["fy_eps_selected_by_parser_count"],
            fy_eps_stored_as_fact_count=ticker_fy_eps_fact_count.get(ticker, 0),
        )
        trace_inputs.append(inp)

    # ── Step 4: Pure classifier ────────────────────────────────────────────────
    trace_result = build_fy_eps_raw_trace(inputs=trace_inputs, extra_errors=errors)

    # ── Step 5: Serialize (cert-gated — no raw SEC payloads, no source URLs) ───
    per_ticker_trace = [
        {
            "ticker": d.ticker,
            "has_research_artifact": d.has_research_artifact,
            "artifact_count": d.artifact_count,
            "latest_artifact_id": d.latest_artifact_id,
            "artifact_fact_count": d.artifact_fact_count,
            "stored_eps_fact_count": d.stored_eps_fact_count,
            "stored_fy_eps_fact_count": d.stored_fy_eps_fact_count,
            "stored_quarterly_eps_fact_count": d.stored_quarterly_eps_fact_count,
            "source_record_count": d.source_record_count,
            "source_10k_accession_count": d.source_10k_accession_count,
            "source_10q_accession_count": d.source_10q_accession_count,
            "source_accessions_include_10k": d.source_accessions_include_10k,
            "raw_companyfacts_fetch_attempted": d.raw_companyfacts_fetch_attempted,
            "raw_companyfacts_fetch_status": d.raw_companyfacts_fetch_status,
            "raw_eps_tag_present_count": d.raw_eps_tag_present_count,
            "raw_eps_unit_keys": d.raw_eps_unit_keys,
            "raw_eps_observation_count": d.raw_eps_observation_count,
            "raw_fy_eps_observation_count": d.raw_fy_eps_observation_count,
            "raw_latest_fy_eps_filed": d.raw_latest_fy_eps_filed,
            "raw_latest_fy_eps_form": d.raw_latest_fy_eps_form,
            "raw_latest_fy_eps_fp": d.raw_latest_fy_eps_fp,
            "raw_latest_fy_eps_has_accn": d.raw_latest_fy_eps_has_accn,
            "fy_eps_filtered_by_unit_count": d.fy_eps_filtered_by_unit_count,
            "fy_eps_filtered_by_source_accession_count": d.fy_eps_filtered_by_source_accession_count,
            "fy_eps_selected_by_parser_count": d.fy_eps_selected_by_parser_count,
            "fy_eps_stored_as_fact_count": d.fy_eps_stored_as_fact_count,
            "fy_eps_extractor_usable_count": d.fy_eps_extractor_usable_count,
            "loss_stage": d.loss_stage,
            "recommended_next_action": d.recommended_next_action,
        }
        for d in trace_result.trace_diagnostics
    ]

    return {
        "trace_version": trace_result.trace_version,
        "safe_for_decision": False,
        "shadow_only": True,
        "read_only": True,
        "diagnostics_only": True,
        "priceband_produced": False,
        "decision_input_mutated": False,
        "visible_decision_changed": False,
        "ttm_computed": False,
        "fy_only": True,
        "tickers_requested": raw_tickers,
        "include_raw_counts_only": request.include_raw_counts_only,
        "raw_sec_fetch_attempted": request.include_raw_counts_only,
        "trace_count": trace_result.trace_count,
        "usable_fy_eps_count": trace_result.usable_fy_eps_count,
        "missing_fy_eps_count": trace_result.missing_fy_eps_count,
        "raw_fetch_attempted_count": trace_result.raw_fetch_attempted_count,
        "raw_fetch_succeeded_count": trace_result.raw_fetch_succeeded_count,
        "loss_stage_counts": trace_result.loss_stage_counts,
        "per_ticker_trace": per_ticker_trace,
        "errors": trace_result.errors,
        "next_step": (
            "Review loss_stage for each ticker and apply the recommended_next_action "
            "to decide Phase 14C.5: fix source accession selection, expand EPS tag/unit "
            "support, fix artifact writer, or classify true unsupported tickers."
        ),
    }


# ── Phase 14D — PriceBand Shadow Policy v1 (shadow-only diagnostics) ────────


@router.post("/priceband-shadow-v1")
async def get_priceband_shadow_v1_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Phase 14D — operator-only PriceBand Shadow Policy v1 diagnostics.

    Classifies certified Phase 14C inputs (source-linked FY EPS + fresh
    market price + sector/industry) into humble, evidence-bounded valuation
    buckets using a static broad-market governance table (policy_static_v1).

    Buckets (positive EPS, broad-market, FY-only earnings yield y_pct = E/P):
      y_pct < 2.0          → expensive
      2.0 <= y_pct < 4.0   → elevated
      4.0 <= y_pct < 6.0   → reasonable
      6.0 <= y_pct < 9.0   → attractive
      y_pct >= 9.0         → unusually_cheap
      EPS < 0              → negative_eps   (NEVER cheap)
      Missing/stale inputs → unavailable

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_PRICEBAND_SHADOW_V1_DIAGNOSTICS_ENABLED=true

    Hard governance invariants:
      - safe_for_decision is always False.
      - shadow_only is always True.
      - visible_snapshot_unchanged is always True.
      - decision_input_mutated is always False.
      - visible_decision_changed is always False.
      - no_target_price_emitted is always True.
      - no_fair_value_emitted is always True.
      - fy_only is always True; ttm_computed is always False.
      - No DecisionInputV3 mutation. No PriceBand enum wiring.
      - No DB writes. No provider calls. No LLM calls.
      - No raw EPS, no raw price, no raw earnings yield numeric values.
      - No buy_below / sell_above / target_price / fair_value keys.

    NEVER called by frontend page load. NEVER called by Intel v3 snapshot
    reads. NEVER imports decide() / decision_policy_v1 / DecisionInputV3 /
    PriceBand. NEVER writes to intel_v3_snapshots or any DB table.
    """
    from datetime import datetime, timedelta, timezone

    settings = get_settings()
    if not settings.intel_v3_priceband_shadow_v1_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_PRICEBAND_SHADOW_V1_DIAGNOSTICS_ENABLED is not enabled",
        )

    db_client = get_supabase_client()
    errors: list[str] = []

    # ── Step 1: Phase 9 SEC metric readiness → company tickers ───────────────
    readiness = compute_sec_readiness_for_phase11_adapter(
        user_id=str(user.id),
        db_client=db_client,
    )
    company_tickers: set[str] = (
        set(readiness.ready_tickers)
        | set(readiness.partial_tickers_with_missing_groups.keys())
        | set(readiness.blocked_tickers_with_reason.keys())
    )
    company_tickers_list = list(company_tickers)

    # ── Step 2: FY EPS facts from research_artifact_facts (source-linked) ────
    fy_diluted_by_ticker: dict[str, tuple[int, float]] = {}
    fy_basic_by_ticker: dict[str, tuple[int, float]] = {}
    eps_source_linked_tickers: set[str] = set()

    if company_tickers_list:
        try:
            art_result = (
                db_client.table("research_artifacts")
                .select("id,ticker")
                .eq("user_id", str(user.id))
                .in_("ticker", company_tickers_list)
                .execute()
            )
            ticker_by_artifact_id: dict[str, str] = {
                str(row["id"]): str(row.get("ticker") or "").upper().strip()
                for row in (art_result.data or [])
                if row.get("id") and row.get("ticker")
            }
            artifact_ids = list(ticker_by_artifact_id.keys())
            if artifact_ids:
                fact_result = (
                    db_client.table("research_artifact_facts")
                    .select("artifact_id,fact_kind,structured_payload,source_id")
                    .eq("user_id", str(user.id))
                    .in_("artifact_id", artifact_ids)
                    .execute()
                )
                for row in (fact_result.data or []):
                    if str(row.get("fact_kind") or "") != "metric_observation":
                        continue
                    sp = row.get("structured_payload")
                    if not isinstance(sp, dict) or sp.get("claim") != "sec_companyfact_observed":
                        continue
                    tag_pre = str(sp.get("tag") or "")
                    if tag_pre not in (
                        "EarningsPerShareDiluted", "EarningsPerShareBasic"
                    ):
                        continue
                    aid = str(row.get("artifact_id") or "")
                    ticker = ticker_by_artifact_id.get(aid, "")
                    if not ticker or ticker not in company_tickers:
                        continue
                    has_source = bool(
                        row.get("source_id") and str(row.get("source_id")).strip()
                    )
                    extraction = extract_fy_eps_observation_from_payload(
                        sp, has_source=has_source
                    )
                    if extraction.skip_reason:
                        continue
                    eps_source_linked_tickers.add(ticker)
                    target = (
                        fy_diluted_by_ticker
                        if extraction.tag == "EarningsPerShareDiluted"
                        else fy_basic_by_ticker
                    )
                    cur = target.get(ticker)
                    if cur is None or extraction.ordering_year > cur[0]:
                        target[ticker] = (
                            extraction.ordering_year, extraction.eps_value
                        )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"research_artifact_facts_query_error: {exc}")

    # ── Step 3: Fresh price + sector/industry from market_snapshots ──────────
    cutoff_date = (
        datetime.now(timezone.utc) - timedelta(days=PSR_PRICE_STALE_THRESHOLD_DAYS)
    ).strftime("%Y-%m-%d")
    price_by_ticker: dict[str, tuple[float, bool]] = {}
    sector_label_by_ticker: dict[str, str] = {}
    industry_label_by_ticker: dict[str, str] = {}

    if company_tickers_list:
        try:
            res = (
                db_client.table("market_snapshots")
                .select("ticker,as_of,price,sector,industry")
                .eq("user_id", str(user.id))
                .in_("ticker", company_tickers_list)
                .order("as_of", desc=True)
                .execute()
            )
            seen: set[str] = set()
            for row in (res.data or []):
                t = str(row.get("ticker") or "").upper().strip()
                if not t or t not in company_tickers or t in seen:
                    continue
                seen.add(t)
                as_of = str(row.get("as_of") or "")
                price_val = row.get("price")
                if price_val is not None and as_of:
                    try:
                        p = float(price_val)
                        is_fresh = as_of[:10] >= cutoff_date
                        price_by_ticker[t] = (p, is_fresh)
                    except (TypeError, ValueError):
                        pass
                sector_val = str(row.get("sector") or "").strip()
                industry_val = str(row.get("industry") or "").strip()
                if sector_val:
                    sector_label_by_ticker[t] = sector_val
                if industry_val:
                    industry_label_by_ticker[t] = industry_val
        except Exception as exc:  # noqa: BLE001
            errors.append(f"market_snapshots_query_error: {exc}")

    # ── Step 4: Build sanitized per-ticker records ───────────────────────────
    records: list[PriceBandShadowInput] = []
    for ticker in company_tickers_list:
        diluted = fy_diluted_by_ticker.get(ticker)
        basic = fy_basic_by_ticker.get(ticker)
        price_entry = price_by_ticker.get(ticker)
        sector_label = sector_label_by_ticker.get(ticker)
        industry_label = industry_label_by_ticker.get(ticker)
        records.append(
            PriceBandShadowInput(
                ticker=ticker,
                fy_diluted_eps=(diluted[1] if diluted is not None else None),
                fy_basic_eps=(basic[1] if basic is not None else None),
                eps_source_linked=(ticker in eps_source_linked_tickers),
                price=(price_entry[0] if price_entry is not None else None),
                price_fresh=(price_entry[1] if price_entry is not None else False),
                sector_available=bool(sector_label),
                industry_available=bool(industry_label),
                sector_label=sector_label,
                industry_label=industry_label,
            )
        )

    # ── Step 5: Pure classification ──────────────────────────────────────────
    result = build_priceband_shadow(records=records, extra_errors=errors)

    # ── Step 6: Build aggregate-safe response ────────────────────────────────
    # Per-ticker rows are cert-gated diagnostics. They MUST NOT include raw
    # EPS values, raw prices, or raw earnings yields.
    per_ticker = [
        {
            "ticker": d.ticker,
            "priceband_policy_version": d.priceband_policy_version,
            "safe_for_decision": d.safe_for_decision,
            "shadow_only": d.shadow_only,
            "visible_decision_changed": d.visible_decision_changed,
            "priceband_produced": d.priceband_produced,
            "valuation_signal": d.valuation_signal,
            "valuation_confidence": d.valuation_confidence,
            "valuation_basis": d.valuation_basis,
            "valuation_policy_table": d.valuation_policy_table,
            "earnings_yield_bucket": d.earnings_yield_bucket,
            "sector": d.sector,
            "industry": d.industry,
            "sector_used_for_classification": d.sector_used_for_classification,
            "broad_fallback_used": d.broad_fallback_used,
            "input_quality": d.input_quality,
            "plain_english_summary": d.plain_english_summary,
            "limitations": d.limitations,
            "unavailable_reason": d.unavailable_reason,
        }
        for d in result.priceband_diagnostics
    ]

    return {
        "adapter_version": result.adapter_version,
        "policy_table_id": result.policy_table_id,
        "policy_basis": result.policy_basis,
        # Hard locks.
        "safe_for_decision": result.safe_for_decision,
        "shadow_only": result.shadow_only,
        "visible_snapshot_unchanged": result.visible_snapshot_unchanged,
        "read_only": result.read_only,
        "diagnostics_only": result.diagnostics_only,
        "decision_input_mutated": result.decision_input_mutated,
        "visible_decision_changed": result.visible_decision_changed,
        "no_target_price_emitted": result.no_target_price_emitted,
        "no_fair_value_emitted": result.no_fair_value_emitted,
        "fy_only": result.fy_only,
        "ttm_computed": result.ttm_computed,
        # Per-ticker diagnostics (cert-gated).
        "priceband_diagnostics": per_ticker,
        # Aggregate counts.
        "evaluated_company_ticker_count": result.evaluated_company_ticker_count,
        "priceband_computed_count": result.priceband_computed_count,
        "priceband_unavailable_count": result.priceband_unavailable_count,
        "by_valuation_signal": result.by_valuation_signal,
        "by_confidence": result.by_confidence,
        "unavailable_reason_counts": result.unavailable_reason_counts,
        "earnings_yield_bucket_counts": result.earnings_yield_bucket_counts,
        "negative_eps_count": result.negative_eps_count,
        "missing_eps_count": result.missing_eps_count,
        "fresh_price_count": result.fresh_price_count,
        "source_linked_eps_count": result.source_linked_eps_count,
        "sector_available_count": result.sector_available_count,
        "industry_available_count": result.industry_available_count,
        "broad_fallback_count": result.broad_fallback_count,
        "recommended_next_step": result.recommended_next_step,
        "errors": result.errors,
    }



# ── Stage 5J — Research Evidence Coverage Read Model v1 (diagnostics) ─────────


class ResearchEvidenceCoverageRequest(BaseModel):
    """Stage 5J — operator request body for read-only evidence coverage summary.

    tickers: optional explicit ticker list. When omitted/empty, the endpoint
    falls back to the runtime-cert user's active portfolio (positions table).
    Max 200 tickers per request.
    """
    tickers: list[str] = []


_MAX_COVERAGE_TICKERS_PER_REQUEST: int = 200


@router.post("/research-evidence-coverage")
async def get_research_evidence_coverage(
    payload: ResearchEvidenceCoverageRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Stage 5J — read-only research evidence coverage summary.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_EVIDENCE_COVERAGE_DIAGNOSTICS_ENABLED=true

    Returns the deterministic Stage 5J coverage summary computed by
    research_evidence_coverage_read_model_v1.compute_research_evidence_coverage().

    Hard guarantees:
      - READ-ONLY. Never triggers an evidence run / LLM / provider call.
      - Never writes to intel_v3_snapshots, recommendations, or research_*.
      - Never imports/calls decide() / decision_policy_v1.
      - Never returns raw artifact payloads, source URLs, fact contents,
        API keys, secrets, or user PII.
      - safe_for_decision is always False in the response.
      - Never called from GET /intel/v3/snapshot or any page load path.
    """
    settings = get_settings()
    if not settings.intel_v3_evidence_coverage_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_EVIDENCE_COVERAGE_DIAGNOSTICS_ENABLED is not enabled",
        )

    from ..services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
        compute_research_evidence_coverage,
    )

    db_client = get_supabase_client()

    requested_raw = payload.tickers or []
    normalized = list(
        dict.fromkeys(t.upper().strip() for t in requested_raw if isinstance(t, str) and t.strip())
    )

    # Fallback: when no tickers are supplied, derive from positions for the
    # cert user. Read-only; failures degrade to an empty portfolio honestly.
    if not normalized:
        try:
            pos_result = (
                db_client.table("positions")
                .select("ticker")
                .eq("user_id", str(user.id))
                .execute()
            )
            for row in (pos_result.data or []):
                if not isinstance(row, dict):
                    continue
                t = row.get("ticker")
                if isinstance(t, str) and t.strip():
                    norm = t.strip().upper()
                    if norm not in normalized:
                        normalized.append(norm)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "research_evidence_coverage_positions_lookup_failed user_id=%s error=%s",
                user.id,
                exc,
            )

    tickers = normalized[:_MAX_COVERAGE_TICKERS_PER_REQUEST]

    summary = compute_research_evidence_coverage(
        user_id=str(user.id),
        tickers=tickers,
        db_client=db_client,
    )
    return summary.to_dict()


# ── Stage 5K — Research Evidence Decision Input Adapter (shadow-only) ─────────


class ResearchEvidenceDecisionReadinessRequest(BaseModel):
    """Stage 5K — operator request body for shadow decision readiness diagnostics.

    tickers: optional explicit ticker list. When omitted/empty, falls back to
    the cert user's active portfolio positions. Max 200 tickers per request.
    """
    tickers: list[str] = []


@router.post("/research-evidence-decision-readiness")
async def get_research_evidence_decision_readiness(
    payload: ResearchEvidenceDecisionReadinessRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Stage 5K — shadow evidence readiness signals for Intel v3 decision axes.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_EVIDENCE_DECISION_READINESS_DIAGNOSTICS_ENABLED=true

    Shadow/diagnostic only. Hard guarantees:
      - READ-ONLY. Calls Stage 5J read model (read-only) then Stage 5K adapter
        (pure, no I/O). No evidence runs, LLM calls, provider calls triggered.
      - NEVER writes to intel_v3_snapshots, recommendations, or research_*.
      - NEVER calls decide() or imports decision_policy_v1.
      - NEVER returns raw artifact payloads, source URLs, fact contents,
        API keys, secrets, or user PII.
      - safe_for_decision is ALWAYS False. shadow_only is ALWAYS True.
      - No visible Buy/Hold/Trim/Sell change.
      - Never called from GET /intel/v3/snapshot or any page-load path.
    """
    settings = get_settings()
    if not settings.intel_v3_evidence_decision_readiness_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_EVIDENCE_DECISION_READINESS_DIAGNOSTICS_ENABLED is not enabled",
        )

    from ..services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
        compute_research_evidence_coverage,
    )
    from ..services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
        compute_decision_input_readiness,
    )

    db_client = get_supabase_client()

    requested_raw = payload.tickers or []
    normalized = list(
        dict.fromkeys(t.upper().strip() for t in requested_raw if isinstance(t, str) and t.strip())
    )

    # Fallback: derive tickers + optional holding context from positions.
    holding_context_by_ticker: dict[str, dict] = {}
    if not normalized:
        try:
            pos_result = (
                db_client.table("positions")
                .select("ticker,category")
                .eq("user_id", str(user.id))
                .execute()
            )
            for row in (pos_result.data or []):
                if not isinstance(row, dict):
                    continue
                t = row.get("ticker")
                if isinstance(t, str) and t.strip():
                    norm = t.strip().upper()
                    if norm not in normalized:
                        normalized.append(norm)
                    holding_context_by_ticker[norm] = {
                        "category": row.get("category") or "",
                    }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "research_evidence_decision_readiness_positions_lookup_failed user_id=%s error=%s",
                user.id,
                exc,
            )

    tickers = normalized[:_MAX_COVERAGE_TICKERS_PER_REQUEST]

    # Stage 5J: compute coverage read model (read-only).
    coverage = compute_research_evidence_coverage(
        user_id=str(user.id),
        tickers=tickers,
        db_client=db_client,
    )

    # Stage 5K: derive shadow axis readiness from coverage (pure, no I/O).
    shadow = compute_decision_input_readiness(
        coverage,
        holding_context_by_ticker=holding_context_by_ticker or None,
    )

    return shadow.to_dict()


# ── Stage 9A — Coverage & Trust Matrix ───────────────────────────────────────


class CoverageTrustMatrixRequest(BaseModel):
    """Stage 9A — operator request body for Coverage & Trust Matrix diagnostics.

    tickers: optional explicit ticker list. When omitted/empty, falls back to
    the cert user's active portfolio positions. Max 200 tickers per request.
    """
    tickers: list[str] = []


@router.post("/coverage-trust-matrix")
async def get_coverage_trust_matrix(
    payload: CoverageTrustMatrixRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Stage 9A — per-ticker Coverage & Trust Matrix (STRONG/PARTIAL/WEAK/MISSING/NOT_APPLICABLE).

    Maps existing Stage 5J coverage lane statuses to a deterministic, asset-type-aware
    per-category trust matrix. This is the foundation diagnostic for Stage 9 — it shows,
    per ticker, whether each research category is strong enough for future synthesis,
    without running any synthesis, LLM, or provider call.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_COVERAGE_TRUST_MATRIX_ENABLED=true

    Hard guarantees:
      - READ-ONLY. Calls Stage 5J read model (read-only DB read) then the Stage 9A
        matrix mapper (pure, no I/O). No evidence runs, LLM calls, or provider calls.
      - NEVER writes to intel_v3_snapshots, recommendations, or research_*.
      - NEVER calls decide() or imports decision_policy_v1.
      - NEVER returns raw artifact payloads, source URLs, fact contents,
        API keys, secrets, or user PII.
      - safe_for_decision is ALWAYS False. synthesis_ready is ALWAYS False.
      - NOT_APPLICABLE categories do not penalize ETF/crypto coverage.
      - WEAK/MISSING blocks are always synthesis-suppressed.
      - No visible Buy/Hold/Trim/Sell change.
      - Never called from GET /intel/v3/snapshot or any page-load path.
    """
    settings = get_settings()
    if not settings.intel_v3_coverage_trust_matrix_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_COVERAGE_TRUST_MATRIX_ENABLED is not enabled",
        )

    from ..services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
        compute_research_evidence_coverage,
    )
    from ..services.intelligence.v3.coverage_trust_matrix_v1 import (
        compute_coverage_trust_matrix,
    )

    db_client = get_supabase_client()

    requested_raw = payload.tickers or []
    normalized = list(
        dict.fromkeys(t.upper().strip() for t in requested_raw if isinstance(t, str) and t.strip())
    )

    # Fallback: derive tickers + holding context from positions.
    holding_context_by_ticker: dict[str, dict] = {}
    if not normalized:
        try:
            pos_result = (
                db_client.table("positions")
                .select("ticker,category")
                .eq("user_id", str(user.id))
                .execute()
            )
            for row in (pos_result.data or []):
                if not isinstance(row, dict):
                    continue
                t = row.get("ticker")
                if isinstance(t, str) and t.strip():
                    norm = t.strip().upper()
                    if norm not in normalized:
                        normalized.append(norm)
                    holding_context_by_ticker[norm] = {
                        "category": row.get("category") or "",
                    }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "coverage_trust_matrix_positions_lookup_failed user_id=%s error=%s",
                user.id,
                exc,
            )

    tickers = normalized[:_MAX_COVERAGE_TICKERS_PER_REQUEST]

    # Stage 5J: compute coverage read model (read-only DB).
    coverage = compute_research_evidence_coverage(
        user_id=str(user.id),
        tickers=tickers,
        db_client=db_client,
    )

    # Stage 9A: map coverage → trust matrix (pure, no I/O).
    matrix = compute_coverage_trust_matrix(
        coverage,
        holding_context_by_ticker=holding_context_by_ticker or None,
    )

    result = matrix.to_dict()
    # Hard-lock safety fields so they cannot drift.
    result["safe_for_decision"] = False
    result["synthesis_ready"] = False
    return result


# ── Stage 9B — Intel Data Foundation Forensics ────────────────────────────────


class DataFoundationForensicsRequest(BaseModel):
    """Stage 9B — operator request body for Data Foundation Forensics.

    tickers: optional explicit ticker list. When omitted/empty, falls back to
    the cert user's active portfolio positions. Max 200 tickers per request.
    """
    tickers: list[str] = []


@router.post("/data-foundation-forensics")
async def get_data_foundation_forensics(
    payload: DataFoundationForensicsRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Stage 9B — per-holding data foundation forensics (root cause classification).

    Inspects actual persisted research artifacts and portfolio data to explain,
    per holding, whether missing data is caused by a provider gap, CIK mapping
    failure, worker/backfill gap, weak readiness, no lane built, or missing
    canonical normalization.

    Required env:
      finance_runtime_cert_enabled=true  + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_DATA_FOUNDATION_FORENSICS_ENABLED=true

    Hard guarantees:
      - READ-ONLY. No evidence runs, LLM calls, or provider calls.
      - NEVER writes to intel_v3_snapshots, recommendations, or research_*.
      - NEVER calls decide() or imports decision_policy_v1.
      - NEVER returns raw artifact payloads, source URLs, fact contents,
        API keys, secrets, or user PII.
      - safe_for_decision is ALWAYS False. synthesis_ready is ALWAYS False.
      - No visible Buy/Hold/Trim/Sell change.
      - Never called from GET /intel/v3/snapshot or any page-load path.
    """
    settings = get_settings()
    if not settings.intel_v3_data_foundation_forensics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INTEL_V3_DATA_FOUNDATION_FORENSICS_ENABLED is not enabled",
        )

    from ..services.intelligence.v3.intel_data_foundation_forensics_v1 import (
        compute_data_foundation_forensics,
    )

    db_client = get_supabase_client()

    requested_raw = payload.tickers or []
    normalized = list(
        dict.fromkeys(t.upper().strip() for t in requested_raw if isinstance(t, str) and t.strip())
    )

    holding_context_by_ticker: dict[str, dict] = {}
    if not normalized:
        try:
            pos_result = (
                db_client.table("positions")
                .select("ticker,category")
                .eq("user_id", str(user.id))
                .execute()
            )
            for row in (pos_result.data or []):
                if not isinstance(row, dict):
                    continue
                t = row.get("ticker")
                if isinstance(t, str) and t.strip():
                    norm = t.strip().upper()
                    if norm not in normalized:
                        normalized.append(norm)
                    holding_context_by_ticker[norm] = {
                        "category": row.get("category") or "",
                    }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "data_foundation_forensics_positions_lookup_failed user_id=%s error=%s",
                user.id,
                exc,
            )

    tickers = normalized[:_MAX_COVERAGE_TICKERS_PER_REQUEST]

    result = compute_data_foundation_forensics(
        user_id=str(user.id),
        tickers=tickers,
        holding_context_by_ticker=holding_context_by_ticker,
        db_client=db_client,
    )

    output = result.to_dict()
    # Hard-lock safety fields so they cannot drift.
    output["safe_for_decision"] = False
    output["synthesis_ready"] = False
    return output


# ── Stage 6 — Evidence-Aware Governance Diagnostics ──────────────────────────


@router.post("/stage6-evidence-governance")
async def get_stage6_evidence_governance_diagnostics(
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
):
    """Stage 6 — evidence-aware governance diagnostics.

    Required env:
      finance_runtime_cert_enabled=true + X-Finance-Runtime-Cert-Secret header
      INTEL_V3_STAGE6_GOVERNANCE_DIAGNOSTICS_ENABLED=true

    What this does:
      1. Loads existing portfolio cards (read-only, no LLM, no provider).
      2. Computes Stage 5J coverage read model (read-only DB read).
      3. Computes Stage 5K shadow adapter (pure, no I/O).
      4. For each ticker: builds DecisionInputV3, applies Stage 6 governance,
         calls decide() deterministically for before/after comparison.
      5. Returns portfolio governance summary: action distribution, HOLD-collapse
         risk, evidence-blocked action counts, per-ticker diagnostics.

    Hard guarantees:
      - READ-ONLY. Does NOT run Intel v3, does NOT write snapshots, artifacts,
        recommendations, or any DB row.
      - Does NOT call LLMs, providers, or evidence workers.
      - Does NOT run on page load. Never called from GET /intel/v3/snapshot.
      - No raw artifact payloads, source URLs, API keys, secrets, or PII.
      - flag_enabled reflects the current INTEL_V3_EVIDENCE_AWARE_POLICY_ENABLED
        setting. Governance is simulated for both off and on states so the
        before/after comparison is always available regardless of flag state.
    """
    settings = get_settings()
    if not settings.intel_v3_stage6_governance_diagnostics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "INTEL_V3_STAGE6_GOVERNANCE_DIAGNOSTICS_ENABLED is not enabled. "
                "Set this flag in the environment to enable Stage 6 governance diagnostics."
            ),
        )

    import asyncio as _asyncio

    from ..services.intelligence.v3.read_only_evidence_adapter import ReadOnlyEvidenceAdapter
    from ..services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
        compute_research_evidence_coverage,
    )
    from ..services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
        compute_decision_input_readiness,
    )
    from ..services.intelligence.v3.intel_v3_evidence_aware_governance_v1 import (
        apply_evidence_governance,
        compute_portfolio_governance_summary,
    )
    from ..services.intelligence.v3.existing_signal_adapter import (
        build_truth_aware_decision_input,
    )
    from ..services.intelligence.v3.decision_policy_v1 import decide
    from ..services.intelligence.v3.portfolio_governor_lite import (
        build_weight_map,
        compute_portfolio_fit,
    )

    db_client = get_supabase_client()
    user_id = str(user.id)
    flag_enabled = settings.intel_v3_evidence_aware_policy_enabled

    # Step 1: load existing portfolio cards (read-only; no LLM, no provider).
    evidence_adapter = ReadOnlyEvidenceAdapter(user_id=user_id)
    try:
        cards, _evidence_stats = await evidence_adapter.load_cards()
    except Exception as exc:
        logger.warning(
            "stage6_governance_diagnostics_load_cards_failed user_id=%s error=%s",
            user_id, exc,
        )
        return {
            "error": "Failed to load portfolio cards",
            "diagnostics_only": True,
            "flag_name": "INTEL_V3_EVIDENCE_AWARE_POLICY_ENABLED",
            "flag_enabled": flag_enabled,
        }

    if not cards:
        return {
            "diagnostics_only": True,
            "flag_name": "INTEL_V3_EVIDENCE_AWARE_POLICY_ENABLED",
            "flag_enabled": flag_enabled,
            "portfolio_ticker_count": 0,
            "message": "No active portfolio cards found.",
        }

    # Step 2: build portfolio weight map (read-only).
    weight_map: dict = {}
    try:
        positions_result = await _asyncio.to_thread(
            lambda: db_client.table("positions")
            .select("ticker,shares,avg_cost")
            .eq("user_id", user_id)
            .execute()
        )
        raw_positions = [
            {"ticker": r["ticker"], "market_value": float(r.get("shares") or 0) * float(r.get("avg_cost") or 0)}
            for r in (positions_result.data or [])
            if isinstance(r, dict) and r.get("ticker")
        ]
        weight_map = build_weight_map(raw_positions)
    except Exception as exc:
        logger.warning(
            "stage6_governance_diagnostics_weight_map_failed user_id=%s error=%s",
            user_id, exc,
        )

    # Step 3: compute Stage 5J coverage + Stage 5K shadow.
    tickers = [c.ticker.upper() for c in cards if hasattr(c, "ticker") and c.ticker]
    holding_context_by_ticker = {
        c.ticker.upper(): {"category": (getattr(c, "category", "") or "")}
        for c in cards
        if hasattr(c, "ticker") and c.ticker
    }

    try:
        coverage = await _asyncio.to_thread(
            lambda: compute_research_evidence_coverage(
                user_id=user_id,
                tickers=tickers,
                db_client=db_client,
            )
        )
        shadow = compute_decision_input_readiness(
            coverage,
            holding_context_by_ticker=holding_context_by_ticker,
        )
    except Exception as exc:
        logger.warning(
            "stage6_governance_diagnostics_shadow_failed user_id=%s error=%s",
            user_id, exc,
        )
        return {
            "error": "Failed to compute evidence shadow",
            "diagnostics_only": True,
            "flag_name": "INTEL_V3_EVIDENCE_AWARE_POLICY_ENABLED",
            "flag_enabled": flag_enabled,
        }

    # Step 4: build before/after decisions for each card.
    import json as _json

    per_ticker_results = []
    action_distribution_off: dict[str, int] = {"BUY": 0, "HOLD": 0, "TRIM": 0, "SELL": 0}
    action_distribution_on: dict[str, int] = {"BUY": 0, "HOLD": 0, "TRIM": 0, "SELL": 0}

    for card in cards:
        ticker = card.ticker
        category = getattr(card, "category", None) or "stock"
        current_pct = weight_map.get(ticker.upper())

        def _safe_json(val):
            if isinstance(val, str):
                try:
                    return _json.loads(val)
                except Exception:
                    return None
            return val

        intel_read = _safe_json(getattr(card, "intel_read", None))
        thesis_v2 = _safe_json(getattr(card, "thesis_v2", None))
        analyst_risks = _safe_json(getattr(card, "analyst_risks", None)) or []
        analyst_drivers = _safe_json(getattr(card, "analyst_drivers", None)) or []

        suppression_reasons: dict = {}

        # Build DecisionInputV3 — same as run_v3 path.
        def _build_inp():
            _inp, _, _ = build_truth_aware_decision_input(
                ticker=ticker,
                action=getattr(card, "action", None),
                analyst_action=getattr(card, "analyst_action", None),
                conviction_level=getattr(card, "conviction_level", None),
                technical_signal=getattr(card, "technical_signal", None),
                risk_flag=getattr(card, "risk_flag", None),
                analyst_risks=analyst_risks,
                category=category,
                data_quality_label=getattr(card, "data_quality_label", None),
                intel_read=intel_read,
                thesis_v2=thesis_v2,
                analyst_used_fallback=getattr(card, "analyst_used_fallback", None),
                primary_driver=getattr(card, "primary_driver", None),
                risk_flag_text=getattr(card, "risk_flag", None),
                action_reason=getattr(card, "action_reason", None),
                analyst_drivers=analyst_drivers,
                asset_type_hint=category,
            )
            if current_pct is not None:
                _inp.portfolio_fit = compute_portfolio_fit(
                    ticker=ticker,
                    category=category,
                    current_pct=current_pct,
                    suppression_reasons=suppression_reasons,
                )
            return _inp

        # Flag-off decision (unchanged).
        inp_off = _build_inp()
        decision_off = decide(inp_off)
        _off_action = decision_off.action.value
        action_distribution_off[_off_action] = action_distribution_off.get(_off_action, 0) + 1

        # Flag-on decision (with governance applied).
        inp_on = _build_inp()
        ticker_readiness = shadow.ticker_readiness.get(ticker.upper())
        gov_result = apply_evidence_governance(
            inp_on,
            ticker_readiness,
            shadow.portfolio_macro,
            flag_enabled=True,  # always simulate governance ON for comparison
        )
        decision_on = decide(inp_on)
        _on_action = decision_on.action.value
        action_distribution_on[_on_action] = action_distribution_on.get(_on_action, 0) + 1

        per_ticker_results.append(gov_result)

    # Step 5: build portfolio governance summary.
    summary = compute_portfolio_governance_summary(
        shadow,
        flag_enabled=flag_enabled,
        per_ticker_results=per_ticker_results,
        action_distribution_off=action_distribution_off,
        action_distribution_on=action_distribution_on,
    )

    result = summary.to_dict()
    result["diagnostics_only"] = True
    return result


# ── Stage 9F.2a — ETF NPORT-P live diagnostic endpoint ──────────────────────


class EtfNportLiveCheckRequest(BaseModel):
    """Stage 9F.2a — operator request body for ETF NPORT-P live diagnostic."""
    tickers: list[str] = []


@router.post("/etf-nport-live-check")
async def etf_nport_live_check(
    payload: EtfNportLiveCheckRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Stage 9F.2a — operator-only diagnostic for SEC EDGAR NPORT-P ETF holdings.

    Calls the same fetch_etf_nport_holdings() provider used in production but
    does NOT write artifacts, does NOT alter decisions or snapshots, and does NOT
    require intel_v3_etf_nport_evidence_enabled=true.

    Required env vars:
      INTEL_V3_NPORT_DIAGNOSTIC_ENDPOINT_ENABLED=true
      FINANCE_RUNTIME_CERT_ENABLED=true + X-Finance-Runtime-Cert-Secret header
      SEC_EDGAR_USER_AGENT=<AppName/version email>

    Returns compact per-ticker JSON with status, CIK, holdings count, sample
    holding names (max 5), and diagnostic fields. Never returns raw XML, raw
    filing body, or the full holdings payload.
    """
    settings = get_settings()
    if not settings.intel_v3_nport_diagnostic_endpoint_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    from ..services.intelligence.research_workers.nport_diagnostic_runner import (
        _NPORT_DIAG_DEFAULT_TICKERS,
        _NPORT_DIAG_MAX_TICKERS,
        run_nport_live_check,
    )
    from ..services.intelligence.research_workers.etf_nport_candidate_discovery import (
        discover_nport_candidates,
    )

    if not settings.sec_edgar_user_agent:
        return {
            "error": "SEC_EDGAR_USER_AGENT not configured.",
            "safe_for_decision": False,
            "visible_snapshot_unchanged": True,
            "diagnostics_only": True,
            "artifact_writes": 0,
        }

    tickers = [t.strip().upper() for t in (payload.tickers or []) if t.strip()]
    if not tickers:
        tickers = list(_NPORT_DIAG_DEFAULT_TICKERS)
    if len(tickers) > _NPORT_DIAG_MAX_TICKERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {_NPORT_DIAG_MAX_TICKERS} tickers per request.",
        )

    # run_nport_live_check is sync; use asyncio.to_thread so the SEC rate-limit
    # sleep does not block the event loop for this operator-only endpoint.
    # discovery_fn=discover_nport_candidates enables SEC EFTS candidate discovery
    # for tickers where the static parent-registrant map fails identity validation.
    # Diagnostic lane only — no artifact writes, no production evidence changes.
    result = await asyncio.to_thread(
        run_nport_live_check,
        tickers,
        settings.sec_edgar_user_agent,
        discovery_fn=discover_nport_candidates,
    )
    logger.info(
        "nport_live_diagnostic_complete total=%d success=%d no_data=%d error=%d user=%s",
        result["tickers_requested"],
        result["tickers_succeeded"],
        result["tickers_no_data"],
        result["tickers_error"],
        getattr(user, "email", "unknown"),
    )
    return result


# ── Stage 9F.2b — ETF Holdings Provider Registry diagnostic endpoint ──────────


class EtfProviderRegistryCheckRequest(BaseModel):
    """Stage 9F.2b — operator request body for ETF provider registry diagnostic."""
    tickers: list[str] = []


@router.post("/etf-provider-registry-check")
async def etf_provider_registry_check(
    payload: EtfProviderRegistryCheckRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Stage 9F.2b — operator-only diagnostic for ETF holdings provider registry.

    Runs the provider registry for the requested ETF universe, trying SEC NPORT
    and issuer-official adapters per ticker. Returns the first identity-verified
    result per ticker and all provider attempt statuses.

    Diagnostic-only: does NOT write artifacts, does NOT alter decisions or
    snapshots, does NOT change Buy/Hold/Trim/Sell. canonical_ready=False and
    safe_for_decision=False for all tickers.

    Required env vars:
      INTEL_V3_ETF_PROVIDER_REGISTRY_DIAGNOSTICS_ENABLED=true
      FINANCE_RUNTIME_CERT_ENABLED=true + X-Finance-Runtime-Cert-Secret header
      SEC_EDGAR_USER_AGENT=<AppName/version email>  (for SEC NPORT calls)

    Returns compact per-ticker JSON with selected_provider_id, providers_attempted,
    provider_statuses, identity_verified, as_of_date, holdings_count, sample_holding_names,
    weights_available, weight_basis, freshness_status, canonical_ready, safe_for_decision.
    """
    settings = get_settings()
    if not settings.intel_v3_etf_provider_registry_diagnostics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    from ..services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        _REGISTRY_DEFAULT_TICKERS,
        _REGISTRY_MAX_TICKERS,
        run_provider_registry_check,
    )

    user_agent = settings.sec_edgar_user_agent or ""
    if not user_agent:
        return {
            "error": "SEC_EDGAR_USER_AGENT not configured — SEC NPORT calls will be skipped.",
            "safe_for_decision": False,
            "canonical_ready": False,
            "diagnostics_only": True,
            "artifact_writes": 0,
        }

    tickers = [t.strip().upper() for t in (payload.tickers or []) if t.strip()]
    if not tickers:
        tickers = list(_REGISTRY_DEFAULT_TICKERS)
    if len(tickers) > _REGISTRY_MAX_TICKERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {_REGISTRY_MAX_TICKERS} tickers per request.",
        )

    result = await asyncio.to_thread(
        run_provider_registry_check,
        tickers,
        user_agent,
    )
    logger.info(
        "etf_provider_registry_check_complete total=%d success=%d "
        "identity_verified=%d no_data=%d error=%d user=%s",
        result["tickers_requested"],
        result["tickers_succeeded"],
        result["tickers_identity_verified"],
        result["tickers_no_data"],
        result["tickers_error"],
        getattr(user, "email", "unknown"),
    )
    return result


# ── Stage 9F.3a — Alpha Vantage ETF_PROFILE entitlement diagnostic ────────────


class AlphaVantageEtfProfileCheckRequest(BaseModel):
    """Stage 9F.3a — operator request body for Alpha Vantage ETF_PROFILE diagnostic."""
    tickers: list[str] = []
    include_controls: bool = False


@router.post("/alpha-vantage-etf-profile-check")
async def alpha_vantage_etf_profile_check(
    payload: AlphaVantageEtfProfileCheckRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Stage 9F.3a — operator-only Alpha Vantage ETF_PROFILE entitlement + shape diagnostic.

    Tests whether Alpha Vantage can provide S-grade ETF holdings data for the
    missing ETF set (XLE, VOO, VTI, VGT, VHT, VIS, VXUS, VYM, SCHD) before
    any canonical adapter is built.

    Diagnostic-only: no artifact writes, no decision mutations, no SQL, no LLM
    calls. canonical_ready=False and safe_for_decision=False always. The API key
    is never logged or returned in any field.

    Fails closed if ALPHA_VANTAGE_API_KEY is not configured.

    Budget guard: at most 11 tickers per run (free quota protection). Default
    run uses 9 tickers (the missing ETF set only). Set include_controls=true
    to add SPY and QQQ as known control tickers (uses 11 ticker budget).

    Warning: do not run more than once per day on the free Alpha Vantage tier
    to avoid burning the per-day API quota.

    Required env vars:
      INTEL_V3_ALPHA_VANTAGE_ETF_PROFILE_DIAGNOSTICS_ENABLED=true
      FINANCE_RUNTIME_CERT_ENABLED=true + X-Finance-Runtime-Cert-Secret header
      ALPHA_VANTAGE_API_KEY=<your key>

    Verdict interpretation:
      candidate_pass    — XLE, SCHD, and >= 5 Vanguard ETFs return holdings+weights+date.
      candidate_partial — Some holdings/weights exist but coverage/schema is incomplete.
      candidate_fail    — Entitlement, rate-limit, no-data, or malformed responses dominate.
    """
    from ..services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import (
        _CONTROL_TICKERS,
        _DEFAULT_TICKERS,
        MAX_TICKERS_PER_RUN,
        run_alpha_vantage_etf_profile_check,
    )

    settings = get_settings()
    if not settings.intel_v3_alpha_vantage_etf_profile_diagnostics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not settings.alpha_vantage_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing_api_key: ALPHA_VANTAGE_API_KEY is not configured",
        )

    # Build ticker list: caller-supplied or default missing set.
    raw_tickers = payload.tickers if payload.tickers else list(_DEFAULT_TICKERS)
    if payload.include_controls:
        for ctrl in _CONTROL_TICKERS:
            if ctrl not in raw_tickers:
                raw_tickers.append(ctrl)

    # Normalize, deduplicate, cap.
    normalized = list(dict.fromkeys(t.upper().strip() for t in raw_tickers if t.strip()))
    tickers = normalized[:MAX_TICKERS_PER_RUN]

    result = run_alpha_vantage_etf_profile_check(
        api_key=settings.alpha_vantage_api_key,
        tickers=tickers,
    )

    logger.info(
        "alpha_vantage_etf_profile_check_endpoint tickers=%d verdict=%s user=%s",
        len(tickers),
        result.get("provider_candidate_verdict", "unknown"),
        getattr(user, "email", "unknown"),
    )
    return result


class FmpEtfHoldingsCheckRequest(BaseModel):
    """Stage 9F.4 — operator request body for FMP ETF holdings diagnostic."""
    tickers: list[str]


@router.post("/fmp-etf-holdings-check")
async def fmp_etf_holdings_check(
    payload: FmpEtfHoldingsCheckRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Stage 9F.4 — operator-only FMP ETF holdings free-key entitlement + shape diagnostic.

    Tests whether the FMP free API key can return ETF holdings with per-holding
    weights and provider as-of/date metadata, before any canonical adapter is built.

    Diagnostic-only: no artifact writes, no decision mutations, no SQL, no LLM
    calls. canonical_ready=False and safe_for_decision=False always. The API key
    is never logged or returned in any field.

    Fails closed if FMP_API_KEY is not configured.

    Proof sequence (one ticker at a time):
      1. {"tickers": ["VOO"]}
      2. {"tickers": ["SCHD"]}
      3. {"tickers": ["VXUS"]}
      4. {"tickers": ["XLE"]}

    Verdict interpretation:
      candidate_pass    — All 4 proof tickers return plausible holdings + weights + date.
      candidate_partial — Some holdings/weights exist but date missing or coverage weak.
      candidate_fail    — Paywalled/unauthorized/no usable holdings returned.

    Required env vars:
      FMP_API_KEY=<your free key>
      FINANCE_RUNTIME_CERT_ENABLED=true + X-Finance-Runtime-Cert-Secret header
    """
    from ..services.intelligence.research_workers.fmp_etf_holdings_runner_v1 import (
        run_fmp_etf_holdings_check,
    )

    settings = get_settings()

    if not settings.fmp_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing_api_key: FMP_API_KEY is not configured",
        )

    if not payload.tickers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="tickers must not be empty — provide at least one ticker symbol",
        )

    # Normalize, deduplicate, cap at 10 (free key quota guard).
    normalized = list(dict.fromkeys(t.upper().strip() for t in payload.tickers if t.strip()))
    tickers = normalized[:10]

    result = run_fmp_etf_holdings_check(
        api_key=settings.fmp_api_key,
        tickers=tickers,
    )

    logger.info(
        "fmp_etf_holdings_check_endpoint tickers=%d verdict=%s user=%s",
        len(tickers),
        result.get("provider_candidate_verdict", "unknown"),
        getattr(user, "email", "unknown"),
    )
    return result


# ── Stage 9K — ETF NPORT artifact-readiness diagnostic endpoint ───────────────


class EtfStage9kArtifactReadinessRequest(BaseModel):
    """Stage 9K — operator request body for artifact-readiness diagnostic."""
    tickers: list[str] = []


@router.post("/etf-stage9k-artifact-readiness")
async def etf_stage9k_artifact_readiness(
    payload: EtfStage9kArtifactReadinessRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Stage 9K — operator-only diagnostic: why are ETF drawers still 'not yet wired'?

    Queries research_artifacts for the requested tickers and reports per-ticker why
    the Stage 9K holdings-ready gate passes or fails.  Does NOT call any provider,
    does NOT write artifacts, does NOT alter decisions or snapshots.

    Five failure modes surfaced:
      1. flag disabled — intel_v3_etf_nport_evidence_enabled=False at runtime
      2. no_artifact_row — no row found for this user_id/ticker/skill_pack
      3. is_active=False — row exists but inactive (production query skips it)
      4. payload_gate_fail — active row found but fetch_status/holdings_count/
                             weights_available/report_period_date/coverage_quality fails
      5. gate_passed — artifact is wired correctly

    Required env vars:
      INTEL_V3_STAGE9K_ARTIFACT_READINESS_DIAGNOSTIC_ENABLED=true
      FINANCE_RUNTIME_CERT_ENABLED=true + X-Finance-Runtime-Cert-Secret header

    Default tickers when none are supplied: VTI, SCHD, VXUS.
    Maximum tickers per request: 20.

    Returns:
      {
        "flag_enabled": bool,
        "user_id": "<uuid>",
        "tickers_requested": int,
        "skill_pack": "<str>",
        "safe_for_decision": false,
        "artifact_writes": 0,
        "diagnostics_only": true,
        "results": [{ per-ticker fields }]
      }
    """
    settings = get_settings()
    if not settings.intel_v3_stage9k_artifact_readiness_diagnostic_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    from ..services.intelligence.v3.etf_stage9k_diagnostic_helper import (
        _NPORT_SKILL_PACK,
        _STAGE9K_DIAG_DEFAULT_TICKERS,
        _STAGE9K_DIAG_MAX_TICKERS,
        build_stage9k_ticker_entry,
    )

    tickers_raw = [t.strip().upper() for t in (payload.tickers or []) if t.strip()]
    if not tickers_raw:
        tickers_raw = list(_STAGE9K_DIAG_DEFAULT_TICKERS)
    tickers_raw = list(dict.fromkeys(tickers_raw))  # deduplicate, preserve order
    if len(tickers_raw) > _STAGE9K_DIAG_MAX_TICKERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {_STAGE9K_DIAG_MAX_TICKERS} tickers per request.",
        )

    flag_enabled: bool = bool(
        getattr(settings, "intel_v3_etf_nport_evidence_enabled", False)
    )
    user_id = str(user.id)
    db_client = get_supabase_client()

    def _query_active() -> list[dict]:
        resp = (
            db_client
            .from_("research_artifacts")
            .select("ticker,skill_pack,artifact_type,is_active,payload")
            .eq("user_id", user_id)
            .eq("skill_pack", _NPORT_SKILL_PACK)
            .eq("is_active", True)
            .in_("ticker", tickers_raw)
            .execute()
        )
        return resp.data or []

    def _query_all() -> list[dict]:
        resp = (
            db_client
            .from_("research_artifacts")
            .select("ticker,skill_pack,artifact_type,is_active,payload")
            .eq("user_id", user_id)
            .eq("skill_pack", _NPORT_SKILL_PACK)
            .in_("ticker", tickers_raw)
            .execute()
        )
        return resp.data or []

    active_rows, all_rows = await asyncio.gather(
        asyncio.to_thread(_query_active),
        asyncio.to_thread(_query_all),
    )

    results = [
        build_stage9k_ticker_entry(t, flag_enabled, active_rows, all_rows)
        for t in tickers_raw
    ]

    gate_passed_count = sum(1 for r in results if r["gate_passed"])
    logger.info(
        "stage9k_artifact_readiness_diagnostic tickers=%d gate_passed=%d flag=%s user=%s",
        len(tickers_raw),
        gate_passed_count,
        flag_enabled,
        getattr(user, "email", "unknown"),
    )

    return {
        "flag_enabled": flag_enabled,
        "user_id": user_id,
        "tickers_requested": len(tickers_raw),
        "skill_pack": _NPORT_SKILL_PACK,
        "safe_for_decision": False,
        "artifact_writes": 0,
        "diagnostics_only": True,
        "results": results,
    }


# ── Stage 9O — Vanguard issuer-official holdings diagnostic endpoint ──────────


class VanguardHoldingsDiagnosticRequest(BaseModel):
    """Stage 9O — operator request body for Vanguard holdings proof diagnostic."""
    tickers: list[str] = []


@router.post("/vanguard-holdings-diagnostic")
async def vanguard_holdings_diagnostic(
    payload: VanguardHoldingsDiagnosticRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Stage 9O — proof diagnostic: can Vanguard issuer-official holdings be canonical?

    Evaluates whether Vanguard issuer-official holdings exports can become a
    future canonical ETF holdings source for VTI, VOO, and VXUS.

    For each ticker, attempts a live CSV fetch from the Vanguard investor portal,
    records exact evidence (fund identity, holdings count, weights, as-of date,
    URL used, parse status), and classifies provider readiness:

      canonical_candidate      — all S-grade criteria verified in this run.
      supplemental_only        — useful data but missing a canonical criterion.
      manual_research_required — source reachable but identity/URL unresolved.
      rejected                 — access failure or unusable data shape.

    Hard S-grade rules:
      missing as_of_date  → supplemental_only (automatic disqualifier).
      missing weights     → supplemental_only (automatic disqualifier).
      identity_not_proven → manual_research_required.
      access_failure      → rejected.

    Proof stage only:
      - No canonical adapter built.
      - No artifact writes.
      - No synthesis.
      - No decision integration.
      - canonical_ready=False and safe_for_decision=False always.
      - No policy changes, no visible product changes, no database changes.

    Required env vars:
      INTEL_V3_VANGUARD_HOLDINGS_DIAGNOSTIC_ENABLED=true
      FINANCE_RUNTIME_CERT_ENABLED=true + X-Finance-Runtime-Cert-Secret header

    Default tickers when none supplied: VTI, VOO, VXUS.
    Maximum tickers per request: 10.

    Returns:
      {
        "diagnostic_version": "stage9o_v1",
        "provider": "issuer_official_vanguard",
        "tickers_requested": int,
        "per_ticker": [{ per-ticker evidence and classification }],
        "summary": { counts by classification },
        "safe_for_decision": false,
        "canonical_ready": false,
        "artifact_writes": 0,
        "diagnostics_only": true,
        "policy_unchanged": true
      }
    """
    settings = get_settings()
    if not settings.intel_v3_vanguard_holdings_diagnostic_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    from ..services.intelligence.research_workers.vanguard_holdings_diagnostic_v1 import (
        _DEFAULT_TICKERS,
        _MAX_TICKERS,
        run_vanguard_holdings_diagnostic,
    )

    tickers_raw = [t.strip().upper() for t in (payload.tickers or []) if t.strip()]
    if not tickers_raw:
        tickers_raw = list(_DEFAULT_TICKERS)
    tickers_raw = list(dict.fromkeys(tickers_raw))  # deduplicate, preserve order
    if len(tickers_raw) > _MAX_TICKERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {_MAX_TICKERS} tickers per request.",
        )

    result = await asyncio.to_thread(
        run_vanguard_holdings_diagnostic,
        tickers_raw,
    )

    logger.info(
        "vanguard_holdings_diagnostic_endpoint tickers=%d canonical_candidate=%d user=%s",
        len(tickers_raw),
        result.get("summary", {}).get("canonical_candidate_count", 0),
        getattr(user, "email", "unknown"),
    )

    return result
