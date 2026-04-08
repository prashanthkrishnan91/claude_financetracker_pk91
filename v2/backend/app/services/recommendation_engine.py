"""Recommendation engine — Buy/Sell/Trim/Hold analysis.

Ported from v1 utils/rec_engine.py with improvements:
- Database-backed instead of in-memory
- Supports persistence and resolution tracking
- Async-native for non-blocking operation
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from ..database import get_supabase_client
from ..models.recommendation import (
    DecisionLogCreate,
    DecisionLogEntry,
    InsightCard,
    RecommendationResolve,
    RecommendationResponse,
)


# ── Classification constants (from v1) ───────────────────────────────────────
FOREVER_HOLD = {"VYM", "SCHD", "VTI"}
DCA_ALWAYS = {"VOO", "QQQ"}
INCOME_FOREVER = {"VYM", "SCHD"}

# Action → CSS color mapping
ACTION_COLORS = {
    "SELL": "red",
    "BUY": "green",
    "TRIM": "gold",
    "HOLD": "blue",
    "REVIEW": "purple",
}


class RecommendationService:
    """Generate, manage, and resolve recommendations."""

    def __init__(self, user_id: UUID):
        self.user_id = user_id
        self.client = get_supabase_client()

    async def get_insight_cards(self) -> list[InsightCard]:
        """Get all active recommendations as frontend-ready InsightCards."""
        recs = (
            self.client.table("recommendations")
            .select("*")
            .eq("user_id", str(self.user_id))
            .eq("is_active", True)
            .order("urgency", desc=True)
            .execute()
        ).data

        # Fetch positions for context
        positions = {
            p["ticker"]: p
            for p in (
                self.client.table("positions")
                .select("*")
                .eq("user_id", str(self.user_id))
                .execute()
            ).data
        }

        cards = []
        for rec in recs:
            pos = positions.get(rec["ticker"], {})
            cards.append(InsightCard(
                id=rec["id"],
                ticker=rec["ticker"],
                name=pos.get("name", rec["ticker"]),
                action=rec["action"],
                detail=rec["detail"],
                rationale=rec.get("rationale", ""),
                urgency=rec["urgency"],
                color=ACTION_COLORS.get(rec["action"], "gray"),
                tax_note=rec.get("tax_note", ""),
                drip_note=rec.get("drip_note", ""),
                current_price=None,  # TODO (Phase 2): enrich with live price
                pnl_pct=None,
                category=pos.get("category", "Unknown"),
            ))

        return cards

    async def refresh(self) -> list[InsightCard]:
        """Re-run the recommendation engine.

        TODO (Phase 2): Implement full recommendation logic from v1's rec_engine.
        Steps:
        1. Deactivate all current active recs
        2. Fetch positions + live prices
        3. Run generate_rec() for each position
        4. Insert new recommendations
        5. Return as InsightCards
        """
        return await self.get_insight_cards()

    async def resolve(self, rec_id: UUID, resolution: RecommendationResolve) -> dict:
        """Resolve a recommendation — accept, reject, defer, or expire."""
        from datetime import datetime, timezone

        update = {
            "is_active": False,
            "resolution": resolution.resolution,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }

        result = (
            self.client.table("recommendations")
            .update(update)
            .eq("id", str(rec_id))
            .eq("user_id", str(self.user_id))
            .execute()
        )

        if not result.data:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Recommendation not found")

        # Log the decision
        if resolution.notes:
            await self.log_decision(DecisionLogCreate(
                recommendation_id=rec_id,
                ticker=result.data[0]["ticker"],
                decision=resolution.resolution,
                notes=resolution.notes,
            ))

        return result.data[0]

    async def list_decisions(self, limit: int = 50) -> list[DecisionLogEntry]:
        """List decision log entries."""
        result = (
            self.client.table("decision_log")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data

    async def log_decision(self, entry: DecisionLogCreate) -> DecisionLogEntry:
        """Create a decision log entry."""
        data = entry.model_dump(mode="json")
        data["user_id"] = str(self.user_id)

        result = self.client.table("decision_log").insert(data).execute()
        return result.data[0]
