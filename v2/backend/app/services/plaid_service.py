"""Plaid service — sync holdings from Robinhood via Plaid Investments API.

Phase 2 will implement the full Plaid integration. This is the service
skeleton that the sync router depends on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from ..database import get_supabase_client


@dataclass
class SyncStatus:
    """Plaid sync status summary."""
    synced_at: Optional[datetime] = None
    holdings_count: int = 0
    cash_balance: float = 0
    status: str = "never_synced"
    age_hours: float = 0

    @property
    def is_fresh(self) -> bool:
        """True if synced within 24 hours."""
        return self.age_hours < 24


class PlaidService:
    """Plaid Investments API integration.

    Calls Plaid only when cache is stale (>24h) or force=True.
    Updates the positions table with authoritative share quantities.
    """

    def __init__(self, user_id: UUID):
        self.user_id = user_id
        self.client = get_supabase_client()

    async def get_last_sync(self) -> Optional[SyncStatus]:
        """Get the most recent Plaid sync entry."""
        result = (
            self.client.table("plaid_sync_log")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("synced_at", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        row = result.data[0]
        synced_at = datetime.fromisoformat(row["synced_at"])
        age = (datetime.now(timezone.utc) - synced_at).total_seconds() / 3600

        return SyncStatus(
            synced_at=synced_at,
            holdings_count=row.get("holdings_count", 0),
            cash_balance=float(row.get("cash_balance", 0)),
            status=row.get("status", "unknown"),
            age_hours=age,
        )

    async def get_sync_status(self) -> dict:
        """Get formatted sync status for the frontend."""
        last = await self.get_last_sync()
        if not last:
            return {
                "status": "never_synced",
                "message": "No Plaid sync recorded — configure API keys first",
            }

        return {
            "status": "fresh" if last.is_fresh else "stale",
            "last_synced_at": last.synced_at,
            "age_hours": round(last.age_hours, 1),
            "holdings_count": last.holdings_count,
            "cash_balance": last.cash_balance,
            "next_sync_in_hours": max(0, round(24 - last.age_hours, 1)),
        }

    async def sync_holdings(self) -> dict:
        """Sync holdings from Plaid.

        TODO (Phase 2): Implement full Plaid sync:
        1. Decrypt user's Plaid credentials
        2. Call Plaid /investments/holdings/get
        3. Parse and normalize holdings
        4. Upsert into positions table
        5. Log to plaid_sync_log
        """
        return {
            "status": "pending",
            "message": "Plaid sync not yet implemented — Phase 2",
        }
