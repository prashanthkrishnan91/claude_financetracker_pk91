"""Portfolio service — summary computation, snapshots, rebalancing."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from ..database import get_supabase_client
from ..models.portfolio import (
    PortfolioSummary,
    RebalanceResult,
    SnapshotResponse,
    TargetAllocationCreate,
    TargetAllocationResponse,
)


class PortfolioService:
    """All portfolio-level business logic."""

    def __init__(self, user_id: UUID):
        self.user_id = user_id
        self.client = get_supabase_client()

    async def get_summary(self) -> PortfolioSummary:
        """Compute portfolio summary from positions + live prices."""
        # Fetch all positions
        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data

        if not positions:
            return PortfolioSummary(
                total_equity=0, total_cost=0, total_pnl=0, total_pnl_pct=0,
                cash_balance=0, day_change=0, day_change_pct=0,
                stocks_value=0, etfs_value=0, crypto_value=0,
                positions_count=0, prices_fresh=0, prices_stale=0,
            )

        # TODO (Phase 2): Fetch live prices and compute real values
        total_cost = sum(float(p["shares"]) * float(p["avg_cost"]) for p in positions)

        return PortfolioSummary(
            total_equity=total_cost,  # Placeholder until price service is live
            total_cost=total_cost,
            total_pnl=0,
            total_pnl_pct=0,
            cash_balance=0,
            day_change=0,
            day_change_pct=0,
            stocks_value=sum(
                float(p["shares"]) * float(p["avg_cost"])
                for p in positions if p["category"] in ("Core", "Other", "IPO")
            ),
            etfs_value=sum(
                float(p["shares"]) * float(p["avg_cost"])
                for p in positions if p["category"] == "ETF"
            ),
            crypto_value=sum(
                float(p["shares"]) * float(p["avg_cost"])
                for p in positions if p["category"] == "Crypto"
            ),
            positions_count=len(positions),
            prices_fresh=0,
            prices_stale=len(positions),
        )

    async def list_snapshots(self, limit: int = 50) -> list[SnapshotResponse]:
        """List portfolio snapshots, newest first."""
        result = (
            self.client.table("portfolio_snapshots")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("snapshot_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data

    async def create_snapshot(self) -> SnapshotResponse:
        """Create a point-in-time snapshot using current data."""
        summary = await self.get_summary()

        # Fetch current positions for the snapshot
        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data

        snapshot = {
            "user_id": str(self.user_id),
            "total_equity": summary.total_equity,
            "total_cost": summary.total_cost,
            "total_pnl": summary.total_pnl,
            "total_pnl_pct": summary.total_pnl_pct,
            "cash_balance": summary.cash_balance,
            "positions_data": positions,
            "metadata": {
                "prices_fresh": summary.prices_fresh,
                "prices_stale": summary.prices_stale,
                "source": "manual_snapshot",
            },
        }

        result = self.client.table("portfolio_snapshots").insert(snapshot).execute()
        return result.data[0]

    async def list_targets(self) -> list[TargetAllocationResponse]:
        """List all target allocations."""
        result = (
            self.client.table("target_allocations")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("ticker")
            .execute()
        )
        return result.data

    async def set_targets(self, targets: list[TargetAllocationCreate]) -> list[TargetAllocationResponse]:
        """Upsert target allocations."""
        results = []
        for t in targets:
            data = {
                "user_id": str(self.user_id),
                "ticker": t.ticker.upper(),
                "target_pct": float(t.target_pct),
            }
            result = (
                self.client.table("target_allocations")
                .upsert(data, on_conflict="user_id,ticker")
                .execute()
            )
            results.extend(result.data)
        return results

    async def calculate_rebalance(self, cash_to_deploy: Optional[float] = None) -> list[RebalanceResult]:
        """Calculate rebalance suggestions based on targets vs current allocation."""
        targets = await self.list_targets()
        if not targets:
            return []

        summary = await self.get_summary()
        total = summary.total_equity + (cash_to_deploy or 0)
        if total <= 0:
            return []

        # Fetch current positions
        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data

        # Build current allocation map
        position_map = {p["ticker"]: float(p["shares"]) * float(p["avg_cost"]) for p in positions}

        results = []
        for t in targets:
            ticker = t["ticker"] if isinstance(t, dict) else t.ticker
            target_pct = float(t["target_pct"] if isinstance(t, dict) else t.target_pct)
            current_value = position_map.get(ticker, 0)
            current_pct = (current_value / total * 100) if total else 0
            drift = current_pct - target_pct
            target_value = total * target_pct / 100
            diff = target_value - current_value

            if abs(drift) < 0.5:
                action = "ON TARGET"
                amount = 0
            elif drift < 0:
                action = f"BUY ${abs(diff):.2f}"
                amount = abs(diff)
            else:
                action = f"SELL ${abs(diff):.2f}"
                amount = -abs(diff)

            results.append(RebalanceResult(
                ticker=ticker,
                current_pct=round(current_pct, 2),
                target_pct=target_pct,
                drift_pct=round(drift, 2),
                suggested_action=action,
                suggested_amount=round(amount, 2),
            ))

        return sorted(results, key=lambda r: r.drift_pct)
