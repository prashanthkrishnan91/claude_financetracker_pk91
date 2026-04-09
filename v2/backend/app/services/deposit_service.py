"""Deposit service — biweekly deployment schedule and execution."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from ..database import get_supabase_client
from ..models.deposit import (
    DepositAllocationFormula,
    DepositPlanCreate,
    DepositPlanExecute,
    DepositPlanResponse,
    DepositSchedule,
)

# Fixed rotation order for the rotating 16% slice
_ROTATION_ORDER = [
    "GOOGL", "META", "AAPL", "MSFT", "NFLX", "CRM",
    "AMD", "BRK-B", "COST", "WMT", "XLE", "VGT",
]

# Fixed allocation breakdown
_BREAKDOWN: dict[str, float] = {
    "NVDA": 0.28,
    "VOO": 0.22,
    "VYM": 0.17,
    "QQQ": 0.17,
    "ROTATING": 0.16,
}

_DEPOSIT_AMOUNT = 900.00
_DEPOSITS_PER_YEAR = 26  # biweekly


def _next_biweekly_friday(from_date: date) -> date:
    """Return the next biweekly Friday from *from_date*.

    Logic per spec:
    - Find the next Friday from today (strictly ahead).
    - If today IS Friday, skip to next Friday first (so next = +7 days),
      then add 2 more weeks (biweekly = +7+14 = +21 days from today).
    - If today is NOT Friday, next Friday = days_until_friday days away,
      then biweekly = next_friday + 1 week.
    """
    from datetime import timedelta

    days_ahead = (4 - from_date.weekday()) % 7
    if days_ahead == 0:
        # Today IS Friday — skip to next Friday, then add 2 more weeks
        next_friday = from_date + timedelta(days=7)
        return next_friday + timedelta(weeks=2)

    next_friday = from_date + timedelta(days=days_ahead)
    # The one after = biweekly
    return next_friday + timedelta(weeks=1)


class DepositService:
    """Biweekly deposit planning and execution."""

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        self.client = get_supabase_client()

    async def get_formula(self) -> DepositAllocationFormula:
        """Return the fixed allocation formula."""
        return DepositAllocationFormula(
            amount=_DEPOSIT_AMOUNT,
            breakdown=_BREAKDOWN,
            rotating_pct=0.16,
            rotation_order=_ROTATION_ORDER,
        )

    async def get_schedule(self) -> DepositSchedule:
        """Fetch deposit plans and compute schedule metadata."""
        result = (
            self.client.table("deposit_plans")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        )
        rows = result.data or []

        upcoming: list[dict] = []
        executed_rows: list[dict] = []

        for row in rows:
            if row.get("executed"):
                executed_rows.append(row)
            else:
                upcoming.append(row)

        # Sort upcoming by deposit_date asc, executed desc
        upcoming.sort(key=lambda r: r.get("deposit_date", ""))
        executed_rows.sort(key=lambda r: r.get("deposit_date", ""), reverse=True)

        # Next biweekly Friday from today
        today = date.today()
        next_deposit_date = _next_biweekly_friday(today)

        # Next rotating pick — cycle through rotation_order by executed count
        executed_count = len(executed_rows)
        next_rotating_pick = _ROTATION_ORDER[executed_count % len(_ROTATION_ORDER)]

        # YTD totals — based on current calendar year
        current_year = today.year
        deployed_ytd = sum(
            float(r.get("amount", 0))
            for r in executed_rows
            if (r.get("executed_at") or r.get("deposit_date") or "").startswith(str(current_year))
        )

        total_annual = _DEPOSITS_PER_YEAR * _DEPOSIT_AMOUNT
        total_remaining_ytd = max(0.0, total_annual - deployed_ytd)

        # Convert rows to DepositPlanResponse objects
        def _to_response(row: dict) -> DepositPlanResponse:
            return DepositPlanResponse(
                id=row["id"],
                user_id=row["user_id"],
                deposit_date=row["deposit_date"],
                amount=row["amount"],
                allocation=row.get("allocation") or {},
                rotating_pick=row.get("rotating_pick"),
                executed=row.get("executed", False),
                executed_at=row.get("executed_at"),
                created_at=row["created_at"],
            )

        return DepositSchedule(
            upcoming=[_to_response(r) for r in upcoming],
            executed=[_to_response(r) for r in executed_rows],
            next_deposit_date=next_deposit_date,
            next_rotating_pick=next_rotating_pick,
            total_deployed_ytd=round(deployed_ytd, 2),
            total_remaining_ytd=round(total_remaining_ytd, 2),
        )

    async def create_plan(self, plan: DepositPlanCreate) -> DepositPlanResponse:
        """Insert a new deposit plan."""
        data = plan.model_dump(mode="json")
        data["user_id"] = str(self.user_id)
        data["executed"] = False

        result = self.client.table("deposit_plans").insert(data).execute()
        row = result.data[0]
        return DepositPlanResponse(**row)

    async def execute_plan(self, plan_id: UUID, execution: DepositPlanExecute) -> DepositPlanResponse:
        """Mark a deposit plan as executed."""
        executed_at = execution.executed_at or datetime.now(timezone.utc)

        result = (
            self.client.table("deposit_plans")
            .update({
                "executed": True,
                "executed_at": executed_at.isoformat(),
            })
            .eq("id", str(plan_id))
            .eq("user_id", str(self.user_id))
            .execute()
        )

        if not result.data:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Deposit plan {plan_id} not found")

        row = result.data[0]
        return DepositPlanResponse(**row)
