"""Recommendation engine — Buy/Sell/Trim/Hold analysis.

Ported from v1 utils/rec_engine.py (v4) with improvements:
- Database-backed instead of in-memory
- Supports persistence and resolution tracking
- Enriched with live prices from the concurrent price engine
- Async-native for non-blocking operation
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

from ..database import get_supabase_client
from ..models.recommendation import (
    AgentInsight,
    AgentRunStatus,
    DecisionLogCreate,
    DecisionLogEntry,
    InsightCard,
    RecommendationResolve,
    StrategyPerformance,
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


# ── Portfolio advisor (LLM-driven) ───────────────────────────────────────────

async def portfolio_advisor(
    portfolio_positions: list[dict[str, Any]],
    macro_summary: str,
    api_key: str,
) -> dict[str, Any]:
    """Generate comprehensive portfolio advice via LLM.

    Takes pre-computed portfolio data and macro context (no fetching inside),
    calls the LLM with the advisor prompt, returns strict JSON format.

    Args:
        portfolio_positions: List of position dicts with ticker, shares, what_changed, etc.
        macro_summary: Current macro context (e.g., inflation, Fed outlook, market sentiment).
        api_key: Anthropic API key for the LLM call.

    Returns:
        Dict with keys: summary, risks, opportunities, cards, top_buys.
        Falls back to safe defaults if LLM fails.
    """
    from .agents.llm import LLMClient

    # Guard: never call the LLM with an empty portfolio
    if not portfolio_positions:
        logger.error("portfolio_advisor: portfolio_positions is empty — skipping LLM call")
        return {
            "summary": "No portfolio positions to analyze.",
            "risks": [],
            "opportunities": [],
            "cards": [],
            "top_buys": [],
        }

    # ── System prompt: role + output schema (no runtime data) ────────────
    system_prompt = """You are a personal portfolio advisor for a long-term retail investor.

Your goal: Provide clear, simple, and actionable investment guidance.

You will receive a JSON object with two keys:
- "macro_summary": string describing current portfolio context
- "portfolio_positions": array of position objects

For each ticker, decide: BUY / HOLD / SELL with high/medium/low confidence.
- SELL = weakening outlook or better opportunities exist
- BUY = strong outlook or worth adding more
- HOLD = neutral or unclear

Keep explanations simple (2 sentences max per ticker).

Return ONLY this valid JSON structure, no other text:
{
  "summary": "2-3 sentence overview in simple language",
  "risks": ["risk1", "risk2"],
  "opportunities": ["opportunity1", "opportunity2"],
  "cards": [
    {
      "ticker": "AAPL",
      "action": "BUY | HOLD | SELL",
      "confidence": "high | medium | low",
      "reasoning": "2 sentences max"
    }
  ],
  "top_buys": ["ticker1", "ticker2", "ticker3"]
}"""

    # ── User message: runtime data as JSON (no template substitution) ─────
    user_payload = json.dumps({
        "macro_summary": macro_summary,
        "portfolio_positions": portfolio_positions,
    })

    # ── Call LLM with fallback handling ──────────────────────────────────
    llm = LLMClient(api_key=api_key)
    result = await llm.ask_json(
        system=system_prompt,
        user=user_payload,
        max_tokens=1024,
    )

    # ── Ensure result matches expected schema ────────────────────────────
    if not result:
        result = {}

    # Safe defaults for missing fields
    return {
        "summary": result.get("summary", "Unable to generate portfolio summary at this time."),
        "risks": result.get("risks", []),
        "opportunities": result.get("opportunities", []),
        "cards": result.get("cards", []),
        "top_buys": result.get("top_buys", []),
    }


# ── Data-quality UX helpers ──────────────────────────────────────────────────


def _derive_confidence_score(conviction: Optional[float]) -> float:
    """Map a conviction score to a data-confidence proxy (0–1).

    Conviction is capped by completeness at write-time, so its magnitude is a
    reliable proxy for how much data backed the recommendation.
    """
    if conviction is None:
        return 0.3
    abs_c = abs(conviction)
    if abs_c >= 0.6:
        return 0.85
    if abs_c >= 0.3:
        return 0.55
    return 0.3


def _derive_quality_label(score: float) -> str:
    if score >= 0.7:
        return "HIGH"
    if score >= 0.45:
        return "MEDIUM"
    return "LOW"


def _derive_reason_tags(rec: dict) -> list[str]:
    """Extract UX reason tags from existing recommendation fields — no extra queries."""
    tags: list[str] = []
    thesis = (rec.get("investment_thesis") or "").lower()
    detail = (rec.get("detail") or "").lower()

    if any(kw in thesis for kw in ("fallback", "low data confidence", "deterministic")):
        tags.append("fallback_used")
    if any(kw in thesis for kw in ("low confidence", "low data", "watchlist", "missing")):
        tags.append("low_data")
    if any(kw in detail for kw in ("unavailable", "api", "limited data")):
        tags.append("api_failure")
    if not tags:
        conviction = rec.get("conviction_score")
        try:
            if conviction is not None and abs(float(conviction)) < 0.3:
                tags.append("low_conviction")
        except (TypeError, ValueError):
            pass
    return tags


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

            conviction = float(rec["conviction_score"]) if rec.get("conviction_score") is not None else None
            data_confidence_score = _derive_confidence_score(conviction)
            data_quality_label = _derive_quality_label(data_confidence_score)
            reason_tags = _derive_reason_tags(rec)

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
                # Agent fields (may be null for legacy rule-based rows)
                investment_thesis=rec.get("investment_thesis"),
                sentiment_score=float(rec["sentiment_score"]) if rec.get("sentiment_score") is not None else None,
                technical_signal=rec.get("technical_signal"),
                conviction_score=conviction,
                suggested_allocation=float(rec["suggested_allocation"]) if rec.get("suggested_allocation") is not None else None,
                agent_run_id=rec.get("agent_run_id"),
                what_changed=rec.get("what_changed"),
                # Data-quality UX fields
                data_confidence_score=data_confidence_score,
                data_quality_label=data_quality_label,
                reason_tags=reason_tags,
            ))

        return cards

    async def queue_agent_run(
        self,
        deposit_amount: Optional[float] = None,
        sale_proceeds: float = 0.0,
    ) -> tuple[str, bool]:
        """Return ``(job_id, is_new)`` for the agent run to drive.

        Reuses an existing run instead of creating a new one when either:
          * a run is already ``queued`` / ``running`` for this user — prevents
            concurrent executions (SEV-1 single-run lock), OR
          * the most recent ``completed`` run finished less than 2 minutes
            ago — light cache to stop retry storms on re-mount.

        STALE JOB RECOVERY: If a 'running' job is >10 minutes old,
        marks it as 'failed' and creates a new run instead.

        Callers (the router) must only dispatch the background pipeline when
        ``is_new`` is True; reused runs already have their lifecycle handled.
        """
        # ── Lock / cache: reuse recent runs when possible ─────────────────
        try:
            recent = (
                self.client.table("agent_runs")
                .select("id, status, started_at, finished_at")
                .eq("user_id", str(self.user_id))
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            ).data or []
        except Exception:
            recent = []

        if recent:
            last = recent[0]
            status = last.get("status")
            started = last.get("started_at")

            # 1) Stale job recovery: if running >10 min, mark failed and create new
            if status == "running" and started:
                if not _within_last(started, seconds=600):
                    logger.warning(
                        "Stale job recovery — marking run %s as failed (running >10 min, started %s)",
                        last["id"], started,
                    )
                    try:
                        self.client.table("agent_runs").update({
                            "status": "failed",
                            "current_agent": "Failed",
                            "progress_pct": 100,
                            "error_message": "Job timeout — running >10 minutes with no progress update",
                            "summary": "Analysis temporarily unavailable — please retry.",
                            "finished_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("id", last["id"]).execute()
                        logger.info("Stale job recovery completed — id=%s", last["id"])
                    except Exception as exc:
                        logger.warning("Failed to mark stale job failed: %s", exc)
                    # Fall through to create new run
                else:
                    # Running but fresh — single-run lock
                    logger.info(
                        "Single-run lock hit — reusing in-flight job %s (status=%s)",
                        last["id"], status,
                    )
                    return last["id"], False

            # 2) Single-run lock (queued): reuse queued job
            elif status == "queued":
                logger.info(
                    "Single-run lock hit — reusing in-flight job %s (status=%s)",
                    last["id"], status,
                )
                return last["id"], False

            # 3) Light cache: completed within the last 2 minutes.
            elif status == "completed":
                finished = last.get("finished_at") or last.get("started_at")
                if finished and _within_last(finished, seconds=120):
                    logger.info(
                        "Cache hit — reusing run %s finished at %s",
                        last["id"], finished,
                    )
                    return last["id"], False

        # Default deposit from user row if not supplied
        if deposit_amount is None:
            try:
                row = (
                    self.client.table("users")
                    .select("deposit_amount")
                    .eq("id", str(self.user_id))
                    .single()
                    .execute()
                )
                deposit_amount = float(row.data.get("deposit_amount") or 900.0)
            except Exception:
                deposit_amount = 900.0

        from .agents.job_runner import build_orchestrator
        orch = build_orchestrator(
            user_id=self.user_id,
            deposit_amount=deposit_amount,
            sale_proceeds=sale_proceeds,
        )
        return await orch.create_run(), True

    async def get_job_status(self, job_id: UUID) -> AgentRunStatus:
        """Fetch the status of an agent run. Used by the UI progress tracker."""
        from fastapi import HTTPException
        row = (
            self.client.table("agent_runs")
            .select("*")
            .eq("id", str(job_id))
            .eq("user_id", str(self.user_id))
            .single()
            .execute()
        )
        if not row.data:
            raise HTTPException(status_code=404, detail="Agent run not found")
        d = row.data
        return AgentRunStatus(
            id=d["id"],
            status=d["status"],
            current_agent=d.get("current_agent"),
            progress_pct=int(d.get("progress_pct") or 0),
            tickers=d.get("tickers") or [],
            deposit_amount=float(d.get("deposit_amount") or 0),
            sale_proceeds=float(d.get("sale_proceeds") or 0),
            allocation=d.get("allocation") or {},
            summary=d.get("summary"),
            error_message=d.get("error_message"),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
        )

    async def get_latest_job(self) -> Optional[AgentRunStatus]:
        """Return the most recent agent run for this user, or None if none exists."""
        rows = (
            self.client.table("agent_runs")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        ).data
        if not rows:
            return None
        d = rows[0]
        return AgentRunStatus(
            id=d["id"],
            status=d["status"],
            current_agent=d.get("current_agent"),
            progress_pct=int(d.get("progress_pct") or 0),
            tickers=d.get("tickers") or [],
            deposit_amount=float(d.get("deposit_amount") or 0),
            sale_proceeds=float(d.get("sale_proceeds") or 0),
            allocation=d.get("allocation") or {},
            summary=d.get("summary"),
            error_message=d.get("error_message"),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
        )

    async def get_agent_insights(self, run_id: Optional[UUID] = None) -> list[AgentInsight]:
        """Fetch the per-ticker agent insights for a run.

        If `run_id` is None, returns the insights from the most recent
        completed run for this user.
        """
        if run_id is None:
            latest = (
                self.client.table("agent_runs")
                .select("id")
                .eq("user_id", str(self.user_id))
                .eq("status", "completed")
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            ).data
            if not latest:
                return []
            run_id = latest[0]["id"]

        rows = (
            self.client.table("agent_insights")
            .select("*")
            .eq("run_id", str(run_id))
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        return [
            AgentInsight(
                id=r["id"],
                run_id=r.get("run_id"),
                ticker=r["ticker"],
                investment_thesis=r.get("investment_thesis"),
                sentiment_score=float(r["sentiment_score"]) if r.get("sentiment_score") is not None else None,
                sentiment_label=r.get("sentiment_label"),
                technical_signal=r.get("technical_signal"),
                technical_summary=r.get("technical_summary"),
                fundamental_score=float(r["fundamental_score"]) if r.get("fundamental_score") is not None else None,
                fundamental_summary=r.get("fundamental_summary"),
                conviction_score=float(r["conviction_score"]) if r.get("conviction_score") is not None else None,
                suggested_allocation=float(r["suggested_allocation"]) if r.get("suggested_allocation") is not None else None,
                suggested_action=r.get("suggested_action"),
                created_at=r.get("created_at"),
                what_changed=r.get("what_changed"),
            )
            for r in rows
        ]

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
        """Create a decision log entry with outcome tracking initialised."""
        data = entry.model_dump(mode="json")
        data["user_id"] = str(self.user_id)
        data.setdefault("status", "active")
        result = self.client.table("decision_log").insert(data).execute()
        return result.data[0]

    async def update_outcomes(self) -> list[DecisionLogEntry]:
        """Refresh current_price, return_pct, and status for all active decision log entries.

        Reuses the existing price service. Marks an entry closed when the
        position for that ticker no longer holds any shares.
        """
        entries = (
            self.client.table("decision_log")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("created_at", desc=True)
            .execute()
        ).data or []

        if not entries:
            return []

        # Current share counts per ticker
        positions = {
            p["ticker"]: float(p.get("shares", 0))
            for p in (
                self.client.table("positions")
                .select("ticker, shares")
                .eq("user_id", str(self.user_id))
                .execute()
            ).data or []
        }

        # Fetch live prices for active entries that have an entry price
        active_tickers = {
            e["ticker"] for e in entries
            if e.get("status", "active") == "active" and e.get("price_at_decision")
        }
        prices: dict[str, float] = {}
        if self._price_service and active_tickers:
            try:
                results = await self._price_service.fetch_prices(list(active_tickers))
                for t, pr in results.items():
                    if pr.is_valid:
                        prices[t] = pr.mid_price
            except Exception:
                pass

        updated: list[DecisionLogEntry] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for entry in entries:
            if entry.get("status", "active") == "closed":
                updated.append(entry)
                continue

            ticker = entry["ticker"]
            entry_price = entry.get("price_at_decision")
            patch: dict = {}

            current_price = prices.get(ticker)
            if current_price is not None:
                patch["current_price"] = current_price
                if entry_price and float(entry_price) > 0:
                    patch["return_pct"] = round(
                        (current_price - float(entry_price)) / float(entry_price) * 100, 4
                    )

            # Close the entry when the position has been fully exited
            shares = positions.get(ticker)
            if shares is None or float(shares) == 0:
                patch["status"] = "closed"
                patch["closed_at"] = now_iso

            if patch:
                row = (
                    self.client.table("decision_log")
                    .update(patch)
                    .eq("id", entry["id"])
                    .execute()
                ).data
                updated.append(row[0] if row else entry)
            else:
                updated.append(entry)

        return updated

    async def get_strategy_performance(self) -> list[StrategyPerformance]:
        """Aggregate decision log entries by strategy_tag."""
        entries = (
            self.client.table("decision_log")
            .select("strategy_tag, return_pct, status")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        groups: dict[str, list] = {}
        for entry in entries:
            tag = entry.get("strategy_tag") or "untagged"
            groups.setdefault(tag, []).append(entry)

        result = []
        for tag, rows in groups.items():
            returns = [float(r["return_pct"]) for r in rows if r.get("return_pct") is not None]
            avg_return = round(sum(returns) / len(returns), 2) if returns else None
            wins = sum(1 for r in returns if r > 0)
            win_rate = round(wins / len(returns) * 100, 1) if returns else None
            result.append(StrategyPerformance(
                strategy_tag=tag,
                avg_return=avg_return,
                win_rate=win_rate,
                total_trades=len(rows),
            ))

        return sorted(result, key=lambda x: x.total_trades, reverse=True)

    async def generate_portfolio_advice(
        self,
        portfolio_positions: list[dict[str, Any]],
        macro_summary: str,
        api_key: str,
    ) -> dict[str, Any]:
        """Generate portfolio advice via the advisor LLM.

        Wraps the standalone portfolio_advisor function with error handling
        and schema validation.
        """
        return await portfolio_advisor(
            portfolio_positions=portfolio_positions,
            macro_summary=macro_summary,
            api_key=api_key,
        )


def _within_last(iso_ts: str, *, seconds: int) -> bool:
    """True if ``iso_ts`` is a timestamp within the last ``seconds`` seconds."""
    if not iso_ts:
        return False
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts) <= timedelta(seconds=seconds)
