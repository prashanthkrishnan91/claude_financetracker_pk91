"""DRIP router — dividend reinvestment analytics."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.drip import DripHistoryEntry, DripPosition, DripSummary

router = APIRouter(prefix="/drip", tags=["drip"])


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


@router.get("/summary", response_model=DripSummary)
async def get_drip_summary(
    user: AuthenticatedUser = Depends(get_current_user),
) -> DripSummary:
    """Return high-level DRIP income analytics."""
    from ..services.drip_service import DripService
    service = DripService(user_id=user.id, price_service=_make_price_service())
    return await service.get_summary()


@router.get("/positions", response_model=list[DripPosition])
async def get_drip_positions(
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[DripPosition]:
    """Return per-position DRIP details with live prices."""
    from ..services.drip_service import DripService
    service = DripService(user_id=user.id, price_service=_make_price_service())
    return await service.get_positions()


@router.get("/history", response_model=list[DripHistoryEntry])
async def get_drip_history(
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[DripHistoryEntry]:
    """Return dividend transaction history (CDIV entries, up to 200)."""
    from ..services.drip_service import DripService
    service = DripService(user_id=user.id)
    return await service.get_history()
