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
from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.agents.job_runner import run_agent_pipeline
from ..services.recommendation_engine import RecommendationService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/diagnostics/finance-intel", tags=["diagnostics"])


class FinanceRuntimeCertRequest(BaseModel):
    mode: Literal["read_only_cards", "force_run_agents", "nonforced_run_agents"]
    deposit_amount: float | None = None
    sale_proceeds: float = 0.0


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
            "poll": {"job_status": f"/api/v1/recommendations/jobs/{job_id}", "insights": f"/api/v1/recommendations/jobs/{job_id}/insights"},
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
