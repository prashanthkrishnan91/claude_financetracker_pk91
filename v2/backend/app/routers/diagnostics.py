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
from ..services.intelligence.research_workers.sec_metric_portfolio_coverage_dry_run import compute_portfolio_sec_metric_coverage
from ..services.intelligence.research_workers.validation_harness import run_validation
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
