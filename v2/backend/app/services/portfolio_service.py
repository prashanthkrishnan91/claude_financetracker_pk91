"""Portfolio service — summary computation, snapshots, rebalancing.

Integrates with the v2 concurrent price engine for live portfolio values.
"""

from __future__ import annotations

from datetime import datetime, timezone
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

    def __init__(self, user_id: UUID, price_service=None):
        self.user_id = user_id
        self.client = get_supabase_client()
        self._price_service = price_service

    async def get_summary(self) -> PortfolioSummary:
        """Compute portfolio summary with live prices.

        Uses the concurrent price engine for real-time data.
        """
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

        # Fetch live prices if price service available
        prices = {}
        fresh_count = 0
        stale_count = 0
        sources_used = set()

        if self._price_service:
            tickers = [p["ticker"] for p in positions]
            try:
                price_results = await self._price_service.fetch_prices(tickers)
                for ticker, pr in price_results.items():
                    if pr.is_valid:
                        prices[ticker] = pr.mid_price
                        if not pr.is_stale:
                            fresh_count += 1
                            sources_used.add(pr.source.split("(")[0])
                        else:
                            stale_count += 1
                    else:
                        stale_count += 1
            except Exception:
                stale_count = len(positions)

        # Compute portfolio values
        total_equity = 0.0
        total_cost = 0.0
        stocks_value = 0.0
        etfs_value = 0.0
        crypto_value = 0.0

        for p in positions:
            shares = float(p["shares"])
            avg_cost = float(p["avg_cost"])
            cost = shares * avg_cost
            total_cost += cost

            price = prices.get(p["ticker"])
            if price:
                market_value = shares * price
            else:
                market_value = cost  # Use cost as fallback

            total_equity += market_value

            cat = p["category"]
            if cat in ("Core", "Other", "IPO", "SELL"):
                stocks_value += market_value
            elif cat == "ETF":
                etfs_value += market_value
            elif cat == "Crypto":
                crypto_value += market_value

        total_pnl = total_equity - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

        # Cash balance from last Plaid sync
        cash = 0.0
        last_sync = (
            self.client.table("plaid_sync_log")
            .select("cash_balance")
            .eq("user_id", str(self.user_id))
            .eq("status", "success")
            .order("synced_at", desc=True)
            .limit(1)
            .execute()
        ).data
        if last_sync:
            cash = float(last_sync[0].get("cash_balance", 0))

        return PortfolioSummary(
            total_equity=round(total_equity + cash, 2),
            total_cost=round(total_cost, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 4),
            cash_balance=round(cash, 2),
            day_change=0,      # TODO: compute from previous close
            day_change_pct=0,  # TODO: compute from previous close
            stocks_value=round(stocks_value, 2),
            etfs_value=round(etfs_value, 2),
            crypto_value=round(crypto_value, 2),
            positions_count=len(positions),
            prices_fresh=fresh_count,
            prices_stale=stale_count,
            last_price_fetch=datetime.now(timezone.utc) if fresh_count > 0 else None,
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
        result = (
            self.client.table("target_allocations")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("ticker")
            .execute()
        )
        return result.data

    async def set_targets(self, targets: list[TargetAllocationCreate]) -> list[TargetAllocationResponse]:
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

        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data

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
