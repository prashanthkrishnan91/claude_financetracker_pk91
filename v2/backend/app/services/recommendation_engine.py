"""Recommendation engine — Buy/Sell/Trim/Hold analysis.

Ported from v1 utils/rec_engine.py (v4) with improvements:
- Database-backed instead of in-memory
- Supports persistence and resolution tracking
- Enriched with live prices from the concurrent price engine
- Async-native for non-blocking operation
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from ..database import get_supabase_client
from ..models.recommendation import (
    DecisionLogCreate,
    DecisionLogEntry,
    InsightCard,
    RecommendationResolve,
)


# ── Classification constants (from v1) ───────────────────────────────────────

INCOME_FOREVER = {"VYM", "SCHD"}
DCA_ALWAYS = {"VOO", "QQQ", "VTI"}

ACTION_COLORS = {
    "SELL": "red",
    "BUY": "green",
    "TRIM": "orange",
    "HOLD": "blue",
    "REVIEW": "purple",
}

# DRIP yield estimates (annual %, approximate 2026 values)
DRIP_YIELD: dict[str, float] = {
    "VYM": 2.8, "SCHD": 3.5, "BND": 3.2, "VWO": 2.1, "VXUS": 1.8,
    "VEA": 1.9, "XLE": 3.0, "VTV": 2.2, "VUG": 0.3, "SPY": 1.2,
    "VGT": 0.4, "VHT": 1.4, "VIS": 1.3, "VTI": 1.3, "VOO": 1.2,
    "QQQ": 0.5, "GLD": 0.0, "QCOM": 2.1, "AAPL": 0.5,
    "MSFT": 0.7, "META": 0.4, "COST": 0.7, "WMT": 0.9, "CRM": 0.0,
    "GOOGL": 0.3, "TSM": 1.4, "AMD": 0.0, "NVDA": 0.1, "NFLX": 0.0,
    "BRK-B": 0.0, "RDDT": 0.0, "ALK": 0.0, "SNOW": 0.0, "CAVA": 0.0,
    "RIVN": 0.0, "BMWYY": 4.5, "BTC": 0.0, "XRP": 0.0, "KLAR": 0.0,
    "BLSH": 0.0, "STUB": 0.0,
}


# ── Rec generation (ported from v1 generate_rec) ────────────────────────────

@dataclass
class RecResult:
    """Internal recommendation result before DB persistence."""
    action_label: str      # Human-readable label with emoji
    action: str            # Normalized: BUY, SELL, TRIM, HOLD, REVIEW
    detail: str
    color: str
    urgency: int
    tax_note: str = ""
    drip_note: str = ""


def _classify_action(label: str) -> str:
    """Map emoji action labels to normalized action types."""
    label_upper = label.upper()
    if any(k in label_upper for k in ("SELL", "🔴")):
        return "SELL"
    if any(k in label_upper for k in ("BUY", "ACCUMULATE", "DCA", "🟢", "🔥", "📈")):
        return "BUY"
    if any(k in label_upper for k in ("TRIM", "✂")):
        return "TRIM"
    if any(k in label_upper for k in ("REVIEW", "ALERT", "STOP-LOSS", "🚨")):
        return "REVIEW"
    return "HOLD"


def _tax_note(lt_ready: bool, lt_date: str, pct_gain: float) -> str:
    """Generate a tax-awareness note."""
    if lt_ready:
        if pct_gain > 20:
            tax_saved = pct_gain * 0.17
            return f"LT eligible — save ~{tax_saved:.0f}% vs ST rate"
        return "LT eligible — sell triggers 15-20% cap gains rate"
    return f"ST status until {lt_date} — selling now triggers 37% ordinary income tax"


def _drip_note(
    ticker: str, drip_shares: float, drip_cost: float,
    current_price: Optional[float],
) -> str:
    """Show DRIP compound growth value."""
    if not drip_shares or not current_price:
        return ""
    drip_val = drip_shares * current_price
    drip_gain = drip_val - drip_cost if drip_cost else drip_val
    yield_pct = DRIP_YIELD.get(ticker, 0)
    note = (
        f"DRIP: {drip_shares:.4f} free shares worth ${drip_val:.2f}"
        f" (${drip_gain:+.2f} gain)"
    )
    if yield_pct:
        note += f" · {yield_pct:.1f}% annual yield"
    return note


def generate_rec(
    cat: str,
    ticker: str,
    cost: float,
    target: Optional[float],
    bear: Optional[float],
    bull: Optional[float],
    lt_ready: bool,
    lt_date: str,
    price: Optional[float],
    drip_shares: float = 0.0,
    drip_cost: float = 0.0,
    divs_received: float = 0.0,
) -> RecResult:
    """Generate a recommendation for a single position.

    Full port of v1 rec_engine.py generate_rec() with all decision branches.
    """

    def _make(label: str, detail: str, color: str, urgency: int,
              tax: str = "", drip: str = "") -> RecResult:
        return RecResult(
            action_label=label,
            action=_classify_action(label),
            detail=detail, color=color, urgency=urgency,
            tax_note=tax, drip_note=drip,
        )

    # ── No price yet ─────────────────────────────────────────────────────
    if not price:
        if cat == "SELL":
            label = "SELL NOW" if lt_ready else f"WAIT — SELL {lt_date}"
            return _make(label, "Sell and consolidate into target ETF", "red", 3)
        return _make("HOLD", "Awaiting live price — tap Refresh", "gray", 0)

    # ── No target (SELL positions) ────────────────────────────────────────
    if not target:
        if cat == "SELL":
            label = "SELL NOW — LT eligible" if lt_ready else f"WAIT — SELL {lt_date}"
            tax = _tax_note(lt_ready, lt_date, (price - cost) / cost * 100 if cost else 0)
            return _make(label, "Consolidate into target ETF per plan", "red", 3, tax)
        return _make("HOLD", "No analyst target set", "gray", 0)

    pct = (price - cost) / cost * 100 if cost else 0
    upside = (target - price) / price * 100 if price else 0
    declining = target < cost
    yield_pct = DRIP_YIELD.get(ticker, 0)
    drip_n = _drip_note(ticker, drip_shares, drip_cost, price)
    tax_n = _tax_note(lt_ready, lt_date, pct)

    # ── Income ETFs — never sell ─────────────────────────────────────────
    if ticker in INCOME_FOREVER:
        annual_income = price * (yield_pct / 100) * (drip_shares or 1)
        return _make(
            "HOLD FOREVER — DRIP on",
            f"Compound income machine. Yield: {yield_pct:.1f}%. "
            f"Est. annual income: ${annual_income:.2f}. Never sell.",
            "purple", 0, "", drip_n,
        )

    # ── Core index ETFs — always DCA ────────────────────────────────────
    if ticker in DCA_ALWAYS:
        return _make(
            "DCA ALWAYS",
            f"Core index — add every biweekly deposit. Never sell. {drip_n}",
            "green", 0, "", drip_n,
        )

    # ── SELL-flagged positions ───────────────────────────────────────────
    if cat == "SELL":
        label = "SELL NOW — LT eligible" if lt_ready else f"WAIT — SELL {lt_date}"
        return _make(label, f"Consolidate into target ETF. {tax_n}", "red", 3, tax_n)

    # ── Bear proximity — highest priority for non-crypto ─────────────────
    if bear and price < bear * 1.10 and cat != "Crypto":
        return _make(
            "STOP-LOSS ALERT",
            f"Price ${price:.2f} within 10% of bear case ${bear:,.0f}. "
            f"Review position immediately. {tax_n}",
            "red", 4, tax_n, drip_n,
        )

    # ── Crypto special rules ─────────────────────────────────────────────
    if cat == "Crypto":
        if upside > 25:
            return _make(
                "ACCUMULATE",
                f"{upside:.0f}% upside to target ${target:,.0f}. Long-term hold.",
                "green", 3,
            )
        if upside < -20:
            return _make(
                "TRIM 15%",
                f"{abs(upside):.0f}% above target. Take some off. LT rate applies.",
                "orange", 2, tax_n,
            )
        return _make(
            "HOLD",
            f"{upside:.0f}% to target ${target:,.0f}. Hold position.",
            "blue", 1,
        )

    # ── Declining thesis (analyst target < cost) ─────────────────────────
    if declining:
        if upside > 20:
            return _make(
                "ACCUMULATE",
                f"{upside:.0f}% to analyst target (below cost — declining thesis). {drip_n}",
                "gold", 2, tax_n, drip_n,
            )
        if 5 >= upside > -10:
            if lt_ready:
                return _make(
                    "TRIM 20% (LT)",
                    f"At analyst target. Take partial profits at LT rate. {tax_n}",
                    "orange", 2, tax_n, drip_n,
                )
            return _make(
                "HOLD (ST)",
                f"Near target — wait for LT: {lt_date}. Avoid 37% tax. {tax_n}",
                "gold", 1, tax_n, drip_n,
            )
        if upside <= -10:
            if lt_ready:
                return _make(
                    "TRIM 25% (LT)",
                    f"Above analyst target. Lock gains at LT rate. {tax_n}",
                    "orange", 2, tax_n, drip_n,
                )
            return _make(
                "HOLD (ST)",
                f"Above target — hold until {lt_date} for LT rate. {tax_n}",
                "gold", 1, tax_n, drip_n,
            )
        return _make(
            "HOLD",
            f"Declining thesis — monitor analyst revisions. {drip_n}",
            "gray", 0, tax_n, drip_n,
        )

    # ── Normal thesis — dip buying ───────────────────────────────────────
    if pct < -20 and upside > 20:
        return _make(
            "STRONG BUY",
            f"Down {abs(pct):.0f}% from cost with {upside:.0f}% to target! "
            f"Maximum opportunity. {drip_n}",
            "green", 4, tax_n, drip_n,
        )

    if pct < -15 and upside > 15:
        return _make(
            "BUY THE DIP",
            f"Down {abs(pct):.0f}% from cost. {upside:.0f}% upside. {drip_n}",
            "green", 3, tax_n, drip_n,
        )

    # ── Standard upside zones ────────────────────────────────────────────
    if upside > 40:
        return _make(
            "ACCUMULATE",
            f"{upside:.0f}% upside — add aggressively on any weakness. {drip_n}",
            "green", 3, tax_n, drip_n,
        )

    if upside > 20:
        if yield_pct > 2.0:
            return _make(
                "ACCUMULATE + DRIP",
                f"{upside:.0f}% price upside + {yield_pct:.1f}% dividend yield. {drip_n}",
                "green", 3, tax_n, drip_n,
            )
        return _make(
            "ACCUMULATE",
            f"{upside:.0f}% upside — buy on weakness. {drip_n}",
            "green", 2, tax_n, drip_n,
        )

    # ── At / above target ────────────────────────────────────────────────
    if 5 >= upside > -10:
        if lt_ready:
            return _make(
                "TRIM 20% (LT)",
                f"At analyst target ${target:,.0f}. Sell 20% at LT rate. {tax_n}",
                "orange", 2, tax_n, drip_n,
            )
        return _make(
            "HOLD (ST)",
            f"Near target — hold until {lt_date} for LT cap-gains rate. {tax_n}",
            "gold", 1, tax_n, drip_n,
        )

    if upside <= -10:
        if lt_ready:
            return _make(
                "TRIM 25% (LT)",
                f"{abs(upside):.0f}% above target. Trim 25% at LT rate. {tax_n}",
                "orange", 2, tax_n, drip_n,
            )
        return _make(
            "HOLD (ST)",
            f"{abs(upside):.0f}% above target — wait for LT: {lt_date}. {tax_n}",
            "gold", 1, tax_n, drip_n,
        )

    # ── IPO lockup ───────────────────────────────────────────────────────
    if cat == "IPO" and not lt_ready:
        return _make(
            "HOLD (IPO)",
            f"IPO lockup — hold until LT: {lt_date}. "
            f"Target ${target:,.0f} ({upside:.0f}% upside).",
            "blue", 0, tax_n,
        )

    # ── Normal hold zone (10-20% upside) ─────────────────────────────────
    if upside > 10:
        return _make(
            "HOLD",
            f"{upside:.0f}% upside to target ${target:,.0f}. {drip_n}",
            "blue", 1, tax_n, drip_n,
        )

    # ── Default ──────────────────────────────────────────────────────────
    return _make(
        "HOLD",
        f"Monitoring — {upside:.0f}% to target ${target:,.0f}. {drip_n}",
        "gray", 0, tax_n, drip_n,
    )


# ── Service class ────────────────────────────────────────────────────────────

class RecommendationService:
    """Generate, manage, and resolve recommendations."""

    def __init__(self, user_id: UUID, price_service=None):
        self.user_id = user_id
        self.client = get_supabase_client()
        self._price_service = price_service

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

        positions = {
            p["ticker"]: p
            for p in (
                self.client.table("positions")
                .select("*")
                .eq("user_id", str(self.user_id))
                .execute()
            ).data
        }

        # Fetch live prices if price service available
        prices: dict[str, float] = {}
        if self._price_service and positions:
            try:
                tickers = list(positions.keys())
                price_results = await self._price_service.fetch_prices(tickers)
                for t, pr in price_results.items():
                    if pr.is_valid:
                        prices[t] = pr.mid_price
            except Exception:
                pass

        cards = []
        for rec in recs:
            ticker = rec["ticker"]
            pos = positions.get(ticker, {})
            price = prices.get(ticker)
            shares = float(pos.get("shares", 0))
            avg_cost = float(pos.get("avg_cost", 0))
            pnl_pct = None
            if price and avg_cost > 0:
                pnl_pct = round((price - avg_cost) / avg_cost * 100, 2)

            cards.append(InsightCard(
                id=rec["id"],
                ticker=ticker,
                name=pos.get("name", ticker),
                action=rec["action"],
                detail=rec["detail"],
                rationale=rec.get("rationale", ""),
                urgency=rec["urgency"],
                color=ACTION_COLORS.get(rec["action"], "gray"),
                tax_note=rec.get("tax_note", ""),
                drip_note=rec.get("drip_note", ""),
                current_price=price,
                pnl_pct=pnl_pct,
                category=pos.get("category", "Unknown"),
            ))

        return cards

    async def refresh(self) -> list[InsightCard]:
        """Re-run the recommendation engine against current positions and prices.

        1. Fetch all positions
        2. Fetch live prices
        3. Run generate_rec() for each position
        4. Deactivate old recommendations
        5. Insert fresh recommendations
        6. Return as InsightCards
        """
        # 1. Fetch positions
        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data

        if not positions:
            return []

        # 2. Fetch live prices
        prices: dict[str, float] = {}
        if self._price_service:
            try:
                tickers = [p["ticker"] for p in positions]
                price_results = await self._price_service.fetch_prices(tickers)
                for t, pr in price_results.items():
                    if pr.is_valid:
                        prices[t] = pr.mid_price
            except Exception:
                pass

        # 3. Generate recommendations for each position
        new_recs = []
        for pos in positions:
            ticker = pos["ticker"]
            price = prices.get(ticker)

            rec = generate_rec(
                cat=pos["category"],
                ticker=ticker,
                cost=float(pos["avg_cost"]),
                target=float(pos["target_price"]) if pos.get("target_price") else None,
                bear=float(pos["bear_price"]) if pos.get("bear_price") else None,
                bull=float(pos["bull_price"]) if pos.get("bull_price") else None,
                lt_ready=bool(pos.get("lt_eligible", False)),
                lt_date=str(pos.get("lt_date", "")) if pos.get("lt_date") else "",
                price=price,
                drip_shares=float(pos.get("drip_shares", 0)),
                drip_cost=float(pos.get("drip_cost", 0)),
                divs_received=float(pos.get("divs_received", 0)),
            )

            new_recs.append({
                "user_id": str(self.user_id),
                "ticker": ticker,
                "action": rec.action,
                "detail": rec.detail,
                "rationale": rec.action_label,
                "urgency": rec.urgency,
                "tax_note": rec.tax_note,
                "drip_note": rec.drip_note,
                "is_active": True,
            })

        # 4. Deactivate all current active recommendations
        self.client.table("recommendations").update({
            "is_active": False,
            "resolution": "expired",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }).eq("user_id", str(self.user_id)).eq("is_active", True).execute()

        # 5. Batch insert fresh recommendations
        if new_recs:
            batch_size = 50
            for i in range(0, len(new_recs), batch_size):
                batch = new_recs[i:i + batch_size]
                self.client.table("recommendations").insert(batch).execute()

        # 6. Return as InsightCards
        return await self.get_insight_cards()

    async def resolve(self, rec_id: UUID, resolution: RecommendationResolve) -> dict:
        """Resolve a recommendation — accept, reject, defer, or expire."""
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
