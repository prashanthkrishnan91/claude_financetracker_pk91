"""AI router — Anthropic-powered portfolio rebalance analysis."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from ..middleware.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/ai", tags=["ai"])


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


@router.get("/rebalance/latest")
async def ai_rebalance_latest(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Optional[dict]:
    """Fetch the most recently stored AI portfolio analysis.

    Returns null if no analysis has been generated yet.
    Used to restore AI analysis state when the user returns to the page.
    """
    from ..services.ai_service import AiService
    service = AiService()
    return await service.get_latest_analysis(user_id=user.id)


@router.post("/rebalance")
async def ai_rebalance(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Generate an AI-powered portfolio rebalance analysis via Claude.

    Returns allocation suggestions and a narrative analysis.
    Requires anthropic_api_key to be set in application settings.
    Result is persisted to the database so it can be restored on page reload.
    """
    from ..services.ai_service import AiService
    service = AiService()
    return await service.generate_rebalance(
        user_id=user.id,
        price_service=_make_price_service(),
    )
