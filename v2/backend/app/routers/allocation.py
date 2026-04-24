"""Allocation router — Deploy tab endpoint.

Runs the portfolio allocation engine against the user's latest compact_v1
analyst insights + current holdings, and returns ranked dollar allocations.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..database import get_supabase_client
from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.allocation_engine import (
    AllocationPlan,
    Holding,
    InsightIn,
    _current_weight,
    _eligibility_reason,
    _get_category,
    _portfolio_total,
    build_allocation_plan,
)
from ..services.recommendation_engine import RecommendationService

router = APIRouter(prefix="/allocation", tags=["allocation"])
logger = logging.getLogger(__name__)

def _format_action_label(raw_action: Any) -> str:
    raw = str(raw_action or "").strip()
    if not raw:
        return "—"

    cleaned = raw
    while cleaned and cleaned[-1].isdigit():
        cleaned = cleaned[:-1].rstrip()
    normalized = cleaned.upper().replace(" ", "_")
    labels = {
        "INITIATE_OR_ADD": "Initiate or Add",
        "ADD_ON_PULLBACKS": "Add on Pullbacks",
        "ACCUMULATE": "Accumulate",
        "ACCUMULATE_GRADUALLY": "Accumulate Gradually",
        "INITIATE_HALF": "Initiate Half",
        "INITIATE_HALF_NOW": "Initiate Half Now",
        "BUY": "Buy",
        "BUY_NOW": "Buy Now",
        "TRIM": "Trim",
        "HOLD": "Hold",
    }
    return labels.get(normalized, cleaned)


def _make_price_service():
    from ..config import get_settings
    from ..services.price_engine import PriceService
    settings = get_settings()
    return PriceService(
        finnhub_key=settings.finnhub_api_key or "",
        alpaca_key=settings.alpaca_api_key or "",
        alpaca_secret=settings.alpaca_secret_key or "",
        polygon_key=settings.polygon_api_key or "",
    )


async def _fetch_holdings(user_id) -> list[Holding]:
    """Pull current positions + live prices, return Holding records."""
    client = get_supabase_client()
    result = (
        client.table("positions")
        .select("ticker,category,shares,avg_cost")
        .eq("user_id", str(user_id))
        .execute()
    )
    rows = result.data or []
    if not rows:
        return []

    tickers = [r["ticker"] for r in rows]
    prices: dict[str, float] = {}
    try:
        ps = _make_price_service()
        pr = await ps.fetch_prices(tickers)
        for t, p in pr.items():
            if p.is_valid:
                prices[t] = p.mid_price
    except Exception as exc:
        logger.warning("allocation: price fetch failed — %s", exc)

    out: list[Holding] = []
    for r in rows:
        ticker = r["ticker"]
        shares = float(r.get("shares") or 0)
        price = prices.get(ticker, 0.0) or float(r.get("avg_cost") or 0.0)
        mv = shares * price
        out.append(Holding(
            ticker=ticker,
            market_value=mv,
            category=r.get("category") or "Core",
        ))
    return out


def _card_to_insight(card: Any) -> InsightIn:
    """Convert an InsightCard (ORM-ish) into the engine input dataclass.

    The InsightCard already carries projected analyst fields
    (analyst_action, analyst_conviction, analyst_confidence,
     analysis_source, reasoning_schema_version, …).
    """
    action = str(
        getattr(card, "analyst_action", None)
        or getattr(card, "action", None)
        or "HOLD"
    ).strip().upper()

    conviction_level = (getattr(card, "conviction_level", None) or "").upper() or None
    conviction_score = (
        getattr(card, "analyst_conviction", None)
        or getattr(card, "conviction", None)
        or getattr(card, "conviction_score", None)
    )
    confidence = (
        getattr(card, "analyst_confidence", None)
        or getattr(card, "confidence", None)
    )
    schema_version = (
        getattr(card, "reasoning_schema_version", None)
        or ""
    ).lower() or None
    analysis_source = (getattr(card, "analysis_source", None) or "").lower() or None
    used_fallback = bool(
        getattr(card, "analyst_used_fallback", False)
        or analysis_source == "deterministic_fallback"
    )
    category = getattr(card, "category", None)
    quality_label = (getattr(card, "data_quality_label", None) or "").upper()

    return InsightIn(
        ticker=card.ticker,
        action=action,
        conviction_level=conviction_level,
        conviction_score=float(conviction_score) if conviction_score is not None else None,
        confidence=float(confidence) if confidence is not None else None,
        schema_version=schema_version,
        analysis_source=analysis_source,
        used_fallback=used_fallback,
        category=category,
        why=getattr(card, "primary_driver", None) or getattr(card, "why_this_matters", None),
        risk=getattr(card, "risk_flag", None),
        do=getattr(card, "action_reason", None),
        alt_view=getattr(card, "differentiation", None),
        thesis=getattr(card, "investment_thesis", None) or getattr(card, "thesis", None),
        stale=quality_label == "LOW" and used_fallback,
    )


def _plan_to_dict(plan: AllocationPlan, *, strategy_mode: str = "allocation_engine") -> dict:
    """Serialize the engine output into the JSON shape the Deploy UI expects."""
    allocations = [
        {
            "ticker": a.ticker,
            "symbol": a.ticker,                 # alias for existing Deploy UI
            "action": a.action,
            "amount": a.amount,
            "current_weight": a.current_weight,
            "after_weight": a.after_weight,
            "target_weight": a.target_weight,
            "conviction_level": a.conviction_level,
            "conviction_score": a.conviction_score,
            "confidence": round(a.confidence * 100.0, 1),
            "score": a.score,
            "reason": a.reason,
            "why": a.why,
            "risk": a.risk,
            "do": a.do,
            "execution_style": a.do,
            "alt_view": a.alt_view,
            "category": a.category,
        }
        for a in plan.allocations
    ]
    exclusions = [{"ticker": e.ticker, "reason": e.reason} for e in plan.exclusions]
    trims = [
        {
            "ticker": t.ticker,
            "action": t.action,
            "current_weight": t.current_weight,
            "reason": t.reason,
        }
        for t in plan.trims
    ]
    return {
        "plan": {
            "cash_to_invest": plan.cash_to_invest,
            "total_deployed": plan.total_deployed,
            "fully_allocated": plan.fully_allocated,
            "strategy": plan.strategy,
        },
        "allocations": allocations,
        "exclusions": exclusions,
        "trims": trims,
        "explanation": plan.portfolio_explanation,
        "warning": plan.warning,
        "summary": {
            "cash_to_invest": plan.cash_to_invest,
            "total_deployed": plan.total_deployed,
            "positions_count": len(allocations),
            "candidates_considered": len(allocations) + len(plan.exclusions),
            "strategy_mode": strategy_mode,
            "fully_allocated": plan.fully_allocated,
        },
    }


@router.get("/plan")
async def get_allocation_plan(
    cash_to_invest: float = Query(default=0.0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Run the allocation engine on the user's latest insights + holdings."""
    holdings = await _fetch_holdings(user.id)

    svc = RecommendationService(user_id=user.id, price_service=_make_price_service())
    try:
        cards = await svc.get_insight_cards()
    except Exception as exc:
        logger.warning("allocation: insight-card fetch failed — %s", exc)
        cards = []

    insights = [_card_to_insight(c) for c in cards]

    # Pull optional target weights (portfolio_targets table — same as Intel)
    targets: dict[str, float] = {}
    try:
        client = get_supabase_client()
        tgt_rows = (
            client.table("portfolio_targets")
            .select("ticker,target_pct")
            .eq("user_id", str(user.id))
            .execute()
        ).data or []
        for row in tgt_rows:
            try:
                targets[str(row["ticker"]).upper()] = float(row["target_pct"])
            except (KeyError, TypeError, ValueError):
                continue
    except Exception:
        targets = {}

    holdings_by_ticker = {h.ticker.upper(): h for h in holdings if h.ticker}
    portfolio_total = _portfolio_total(holdings)
    eligible_before_allocation = 0
    for ins in insights:
        tkr = (ins.ticker or "").upper()
        holding = holdings_by_ticker.get(tkr)
        category = _get_category(tkr, ins, holding)
        current_weight = _current_weight(
            holding.market_value if holding else 0.0,
            portfolio_total,
        )
        target_weight = targets.get(tkr, 0.0)
        if _eligibility_reason(
            ins,
            current_weight=current_weight,
            target_weight=target_weight,
            category=category,
        ) is None:
            eligible_before_allocation += 1
    logger.info("allocation: eligible candidates before allocation=%d", eligible_before_allocation)

    plan = build_allocation_plan(
        cash_to_invest=cash_to_invest,
        holdings=holdings,
        insights=insights,
        target_weights=targets,
    )
    cards_by_ticker = {str(getattr(c, "ticker", "")).upper(): c for c in cards}
    for exclusion in plan.exclusions[:5]:
        card = cards_by_ticker.get(exclusion.ticker.upper())
        raw_action = (
            getattr(card, "analyst_action", None)
            or getattr(card, "action", None)
            or None
        ) if card else None
        logger.info(
            "allocation: rejected ticker=%s reason=%s raw_action=%s display_action=%s confidence=%s data_quality=%s analysis_freshness=%s",
            exclusion.ticker,
            exclusion.reason,
            raw_action,
            _format_action_label(raw_action),
            getattr(card, "analyst_confidence", None) if card else None,
            getattr(card, "data_quality_label", None) if card else None,
            getattr(card, "analysis_source", None) if card else None,
        )
    return _plan_to_dict(plan)
