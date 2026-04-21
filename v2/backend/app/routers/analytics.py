"""Analytics router — strategy performance aggregations."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.recommendation import StrategyPerformance
from ..services.recommendation_engine import RecommendationService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/strategy-performance", response_model=list[StrategyPerformance])
async def get_strategy_performance(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return decision log stats grouped by strategy_tag."""
    service = RecommendationService(user_id=user.id)
    return await service.get_strategy_performance()
