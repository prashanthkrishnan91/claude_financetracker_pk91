"""Diagnostics router — env-gated runtime certification harness for Finance Intel."""

from __future__ import annotations

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


class SecFyEpsBackfillRequest(BaseModel):
    """Phase 14C.2 — operator request body for SEC FY EPS coverage backfill.

    Re-runs the earnings reviewer for an explicit list of tickers so that
    research_artifact_facts is regenerated with the FY EPS coverage policy.
    dry_run=True (default): returns what would be re-run without writing.
    dry_run=False: calls run_earnings_reviewer_dark for each ticker.
    """
    tickers: list[str] = []
    dry_run: bool = True


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
