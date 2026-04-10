"""Recommendations router — insight cards, decision log."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.recommendation import (
    DecisionLogCreate,
    DecisionLogEntry,
    InsightCard,
    RecommendationResolve,
    RecommendationResponse,
)
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
    """Get all active recommendations as frontend-ready InsightCards.

    This is the primary recommendations endpoint — combines
    recommendation data with current prices and position context.
    """
    service = RecommendationService(user_id=user.id, price_service=_make_price_service())
    cards = await service.get_insight_cards()

    if action:
        cards = [c for c in cards if c.action == action.upper()]

    return cards


@router.post("/refresh", response_model=list[InsightCard])
async def refresh_recommendations(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Re-run the recommendation engine against current positions and prices.

    Deactivates stale recommendations and generates fresh ones.
    """
    service = RecommendationService(user_id=user.id, price_service=_make_price_service())
    return await service.refresh()


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


@router.post("/decisions", response_model=DecisionLogEntry, status_code=201)
async def log_decision(
    entry: DecisionLogCreate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Log a manual decision on a recommendation."""
    service = RecommendationService(user_id=user.id)
    return await service.log_decision(entry)
