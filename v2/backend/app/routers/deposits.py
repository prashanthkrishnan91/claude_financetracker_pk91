"""Deposits router — biweekly deployment schedule and execution."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.deposit import (
    DepositAllocationFormula,
    DepositPlanCreate,
    DepositPlanExecute,
    DepositPlanResponse,
    DepositSchedule,
)

router = APIRouter(prefix="/deposits", tags=["deposits"])


@router.get("/deposit-plan")
async def get_deposit_plan_route(
    cash_to_invest: float = Query(default=900.0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Compute next deposit plan with personalization and strategy layers."""
    from ..services.deposit_service import DepositService
    from ..services.agents.job_runner import _user_keys

    keys = _user_keys(user.id)
    service = DepositService(user_id=user.id)
    return await service.get_deposit_plan(snapshot={}, api_key=keys.get("anthropic", ""))


@router.get("/schedule", response_model=DepositSchedule)
async def get_deposit_schedule(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the full deposit schedule — upcoming + executed."""
    from ..services.deposit_service import DepositService
    service = DepositService(user_id=user.id)
    return await service.get_schedule()


@router.get("/formula", response_model=DepositAllocationFormula)
async def get_allocation_formula(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the current deposit allocation formula.

    Default: NVDA 28% / VOO 22% / VYM 17% / QQQ 17% / Rotating 16%
    """
    from ..services.deposit_service import DepositService
    service = DepositService(user_id=user.id)
    return await service.get_formula()


@router.post("/", response_model=DepositPlanResponse, status_code=201)
async def create_deposit_plan(
    plan: DepositPlanCreate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a new deposit plan with allocation breakdown."""
    from ..services.deposit_service import DepositService
    service = DepositService(user_id=user.id)
    return await service.create_plan(plan)


@router.patch("/{plan_id}/execute", response_model=DepositPlanResponse)
async def execute_deposit(
    plan_id: UUID,
    execution: DepositPlanExecute,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Mark a deposit plan as executed."""
    from ..services.deposit_service import DepositService
    service = DepositService(user_id=user.id)
    return await service.execute_plan(plan_id, execution)
