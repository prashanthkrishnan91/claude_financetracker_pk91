"""Allocation router — Deploy tab endpoint.

Runs the portfolio allocation engine against the user's latest compact_v1
analyst insights + current holdings, and returns ranked dollar allocations.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..database import get_supabase_client
from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.adaptive_deployment import (
    AdaptiveDecision,
    StagedAllocation,
    adapt_allocation_plan,
)
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
from ..services.decision_log_service import DecisionLogService
from ..services.deployment_engine import DeploymentDecision, classify_deployment
from ..services.recommendation_engine import RecommendationService
from ..services.regime_engine import RegimeOutput, detect_market_regime

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


def _plan_to_dict(
    plan: AllocationPlan,
    *,
    strategy_mode: str = "allocation_engine",
    regime: RegimeOutput | None = None,
    adaptive: AdaptiveDecision | None = None,
    deployment_v2: DeploymentDecision | None = None,
) -> dict:
    """Serialize the engine output into the JSON shape the Deploy UI expects."""
    staged_by_ticker: dict[str, StagedAllocation] = {}
    if adaptive is not None:
        staged_by_ticker = {
            s.ticker.upper(): s for s in adaptive.staged_allocations
        }

    # Build v2 per-ticker lookup for O(1) access
    v2_by_ticker: dict[str, Any] = {}
    if deployment_v2 is not None:
        for pt in deployment_v2.per_ticker_allocations:
            v2_by_ticker[pt.ticker.upper()] = pt

    allocations: list[dict[str, Any]] = []
    for a in plan.allocations:
        staged = staged_by_ticker.get(a.ticker.upper())
        v2t = v2_by_ticker.get(a.ticker.upper())
        # v2 canonical immediate/reserve; fall back to adaptive staging, then full deploy
        immediate = v2t.deploy_now if v2t is not None else (staged.immediate_amount if staged else a.amount)
        reserve = v2t.reserve if v2t is not None else (staged.reserve_amount if staged else 0.0)
        row: dict[str, Any] = {
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
            # Canonical deploy-now / reserve (v2 when available, adaptive fallback)
            "immediate_amount": immediate,
            "reserve_amount": reserve,
            "staging_instruction": staged.staging_instruction if staged else None,
            "execution_timing": staged.execution_timing if staged else None,
        }
        # Additive v2 per-ticker fields
        if v2t is not None:
            row["ticker_role"] = v2t.role
            row["capped"] = v2t.capped
            row["cap_reason"] = v2t.cap_reason
        allocations.append(row)
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
    plan_block: dict[str, Any] = {
        "cash_to_invest": plan.cash_to_invest,
        "total_deployed": plan.total_deployed,
        "fully_allocated": plan.fully_allocated,
        "strategy": plan.strategy,
    }
    if adaptive is not None:
        plan_block["recommended_deploy_amount"] = adaptive.recommended_deploy_amount
        plan_block["cash_reserve"] = adaptive.cash_reserve_amount
        plan_block["deploy_percentage"] = adaptive.deploy_percentage
        plan_block["deployment_mode"] = adaptive.deployment_mode
    if deployment_v2 is not None:
        # v2 canonical amounts — also override backward-compat fields so the
        # existing Deploy UI picks up the v2 values transparently.
        plan_block["deploy_now_amount"] = deployment_v2.deploy_now_amount
        plan_block["reserve_amount"] = deployment_v2.reserve_amount
        plan_block["deployment_mode_v2"] = deployment_v2.deployment_mode
        plan_block["deployment_confidence"] = deployment_v2.deployment_confidence
        plan_block["deployment_reason"] = deployment_v2.deployment_reason
        plan_block["cash_drag_penalty_applied"] = deployment_v2.cash_drag_penalty_applied
        plan_block["reserve_reason"] = deployment_v2.reserve_reason
        # Override backward-compat fields with v2 canonical values
        plan_block["recommended_deploy_amount"] = deployment_v2.deploy_now_amount
        plan_block["cash_reserve"] = deployment_v2.reserve_amount

    explanation = plan.portfolio_explanation
    if adaptive is not None and adaptive.adaptive_reasons:
        explanation = " ".join(adaptive.adaptive_reasons)

    out: dict[str, Any] = {
        "plan": plan_block,
        "allocations": allocations,
        "exclusions": exclusions,
        "trims": trims,
        "explanation": explanation,
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
    if regime is not None:
        out["regime"] = {
            "regime_label": regime.regime_label,
            "regime_score": regime.regime_score,
            "regime_reasons": regime.regime_reasons,
            "data_quality": regime.data_quality,
        }
    if adaptive is not None:
        out["adaptive"] = {
            "deploy_percentage": adaptive.deploy_percentage,
            "deployment_mode": adaptive.deployment_mode,
            "recommended_deploy_amount": adaptive.recommended_deploy_amount,
            "cash_reserve_amount": adaptive.cash_reserve_amount,
            "adaptive_reasons": adaptive.adaptive_reasons,
            "adjustments_applied": adaptive.adjustments_applied,
            "style_messages": adaptive.style_messages,
            "behavior_profile": adaptive.behavior_profile,
        }
    if deployment_v2 is not None:
        out["deployment_v2"] = {
            "total_deposit": deployment_v2.total_deposit,
            "deploy_now_amount": deployment_v2.deploy_now_amount,
            "reserve_amount": deployment_v2.reserve_amount,
            "deployment_mode": deployment_v2.deployment_mode,
            "deployment_confidence": deployment_v2.deployment_confidence,
            "deployment_reason": deployment_v2.deployment_reason,
            "cash_drag_penalty_applied": deployment_v2.cash_drag_penalty_applied,
            "reserve_reason": deployment_v2.reserve_reason,
            "reserve_trigger": (
                dataclasses.asdict(deployment_v2.reserve_trigger)
                if deployment_v2.reserve_trigger else None
            ),
            "risks": deployment_v2.risks,
            "data_quality": deployment_v2.data_quality,
            "evaluation_notes_for_future_decision_log": deployment_v2.evaluation_notes_for_future_decision_log,
            "deployment_score": deployment_v2.deployment_score,
            "adjustments_applied": deployment_v2.adjustments_applied,
        }
    return out


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

    # Adaptive layer — regime detection + deploy %/reserve staging.
    regime: RegimeOutput | None = None
    adaptive: AdaptiveDecision | None = None
    behavior_profile: dict[str, Any] = {}
    try:
        regime = await detect_market_regime()
    except Exception as exc:  # noqa: BLE001 — never fail Deploy on regime
        logger.warning("allocation: regime detection failed — %s", exc)
        regime = None
    try:
        behavior_profile = DecisionLogService().getUserBehaviorProfile(str(user.id), limit=10)
    except Exception as exc:  # noqa: BLE001 — deploy should work without decision history
        logger.warning("allocation: behavior profile fetch failed — %s", exc)
        behavior_profile = {}
    try:
        adaptive = adapt_allocation_plan(
            cash_to_deploy=cash_to_invest,
            allocations=plan.allocations,
            regime=regime,  # type: ignore[arg-type]
            holdings=holdings,
            portfolio_total=portfolio_total,
            user_behavior=behavior_profile,
        )
    except Exception as exc:  # noqa: BLE001 — adaptive layer is optional
        logger.warning("allocation: adaptive decision failed — %s", exc)
        adaptive = None
    if regime is not None and adaptive is not None:
        logger.info(
            "allocation: adaptive regime=%s score=%.0f deploy_pct=%.1f mode=%s",
            regime.regime_label, regime.regime_score,
            adaptive.deploy_percentage, adaptive.deployment_mode,
        )

    deployment_v2: DeploymentDecision | None = None
    try:
        deployment_v2 = classify_deployment(
            cash_to_deploy=cash_to_invest,
            allocations=plan.allocations,
            regime=regime,  # type: ignore[arg-type]
            holdings=holdings,
            portfolio_total=portfolio_total,
        )
        logger.info(
            "allocation: v2 mode=%s deploy_now=$%.0f reserve=$%.0f score=%.1f",
            deployment_v2.deployment_mode,
            deployment_v2.deploy_now_amount,
            deployment_v2.reserve_amount,
            deployment_v2.deployment_score,
        )
    except Exception as exc:  # noqa: BLE001 — v2 classifier is optional; never fail Deploy
        logger.warning("allocation: v2 deployment classification failed — %s", exc)
        deployment_v2 = None

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
    return _plan_to_dict(plan, regime=regime, adaptive=adaptive, deployment_v2=deployment_v2)
