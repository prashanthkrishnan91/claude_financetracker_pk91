"""Recommendations router — insight cards, agent runs, decision log.

The `refresh` endpoint no longer runs a synchronous rule engine — it queues
a multi-agent pipeline via FastAPI BackgroundTasks and returns a job_id.
The UI polls `/recommendations/jobs/{job_id}` to drive the progress tracker.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.recommendation import (
    AgentInsight,
    AgentRunCreate,
    AgentRunQueued,
    AgentRunStatus,
    DecisionLogCreate,
    DecisionLogEntry,
    InsightCard,
    RecommendationResolve,
)
from ..services.agents.job_runner import run_agent_pipeline
from ..services.recommendation_engine import RecommendationService

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _make_price_service():
    from ..services.price_engine import PriceService
    from ..config import get_settings
    settings = get_settings()
    return PriceService(
        finnhub_key=settings.finnhub_api_key or "",
        alpaca_key=settings.alpaca_api_key or "",
        alpaca_secret=settings.alpaca_secret_key or "",
        polygon_key=settings.polygon_api_key or "",
    )


@router.get("/", response_model=list[InsightCard])
async def list_active_recommendations(
    action: str | None = Query(default=None, description="Filter: BUY|SELL|TRIM|HOLD|REVIEW"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get all active recommendations as frontend-ready InsightCards."""
    service = RecommendationService(user_id=user.id, price_service=_make_price_service())
    cards = await service.get_insight_cards()

    if action:
        cards = [c for c in cards if c.action == action.upper()]

    return cards


@router.post("/refresh", response_model=AgentRunQueued, status_code=202)
async def refresh_recommendations(
    background_tasks: BackgroundTasks,
    payload: AgentRunCreate | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Queue a multi-agent pipeline run.

    Returns immediately with a job_id. The pipeline runs in the background
    and writes progress to the agent_runs table; poll
    `/recommendations/jobs/{job_id}` for live status.
    """
    payload = payload or AgentRunCreate()
    service = RecommendationService(user_id=user.id)
    job_id, is_new = await service.queue_agent_run(
        deposit_amount=payload.deposit_amount,
        sale_proceeds=payload.sale_proceeds or 0.0,
    )
    if is_new:
        # Hand off to FastAPI BackgroundTasks — fire-and-forget, UI polls status.
        background_tasks.add_task(
            run_agent_pipeline,
            user.id,
            job_id,
            payload.deposit_amount if payload.deposit_amount is not None else 900.0,
            payload.sale_proceeds or 0.0,
        )
        return AgentRunQueued(
            job_id=job_id,
            status="queued",
            message="Agent pipeline queued — poll /recommendations/jobs/{job_id}",
        )
    # Single-run lock or light cache hit — return the existing run without
    # dispatching a new pipeline. Frontend polls the same job_id.
    return AgentRunQueued(
        job_id=job_id,
        status="reused",
        message="Reusing recent agent run — poll /recommendations/jobs/{job_id}",
    )


@router.get("/jobs/latest", response_model=AgentRunStatus | None)
async def get_latest_job(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return the most recent agent run for this user (any status).

    Used by the UI on mount to restore the progress tracker if a job is still
    running, or to confirm the last completed run.
    """
    service = RecommendationService(user_id=user.id)
    return await service.get_latest_job()


@router.get("/jobs/{job_id}", response_model=AgentRunStatus)
async def get_job_status(
    job_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Poll an in-flight or completed agent run. Drives the progress tracker UI."""
    service = RecommendationService(user_id=user.id)
    return await service.get_job_status(job_id)


@router.get("/jobs/{job_id}/insights", response_model=list[AgentInsight])
async def get_run_insights(
    job_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the full per-ticker agent insights for a specific run."""
    service = RecommendationService(user_id=user.id)
    return await service.get_agent_insights(run_id=job_id)


@router.get("/insights/latest", response_model=list[AgentInsight])
async def get_latest_insights(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the agent insights from the most recent completed run."""
    service = RecommendationService(user_id=user.id)
    return await service.get_agent_insights(run_id=None)


@router.patch("/{rec_id}/resolve")
async def resolve_recommendation(
    rec_id: UUID,
    resolution: RecommendationResolve,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Accept, reject, or defer a recommendation."""
    service = RecommendationService(user_id=user.id)
    return await service.resolve(rec_id, resolution)


@router.get("/decisions", response_model=list[DecisionLogEntry])
async def list_decisions(
    limit: int = Query(default=50, le=500),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the decision log — history of actions taken on recommendations."""
    service = RecommendationService(user_id=user.id)
    return await service.list_decisions(limit=limit)


@router.get("/decisions/outcomes", response_model=list[DecisionLogEntry])
async def get_decision_outcomes(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Refresh and return outcome tracking for all decision log entries.

    Updates current_price, return_pct, and closed status using live prices,
    then returns the full decision log with outcome data.
    """
    service = RecommendationService(user_id=user.id, price_service=_make_price_service())
    return await service.update_outcomes()


@router.post("/decisions", response_model=DecisionLogEntry, status_code=201)
async def log_decision(
    entry: DecisionLogCreate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Log a manual decision on a recommendation."""
    service = RecommendationService(user_id=user.id)
    return await service.log_decision(entry)
