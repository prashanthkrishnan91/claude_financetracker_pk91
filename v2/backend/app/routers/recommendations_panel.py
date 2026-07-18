"""Recommendations panel router — deterministic Intel v3 actions + rationale.

Read-only. Wraps the latest certified Intel v3 snapshot (the single decision
authority) with the rationale composer. No LLM calls, no writes, no action
recomputation. A recommendation with no rationale is never returned.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from ..config import get_settings
from ..database import get_supabase_client
from ..middleware.auth import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/panel")
async def get_recommendations_panel(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Current Buy/Hold/Trim/Sell calls from Intel v3 with one-line rationale."""
    from ..services.intelligence.v3.intel_v3_service import IntelV3Service
    from ..services.recommendation_rationale_v1 import build_recommendation_panel
    from ..services.tax_lot_engine import (
        build_tax_lots,
        enrich_lots_with_market,
        summarize_ticker_lots,
    )

    settings = get_settings()
    client = get_supabase_client()

    # 1. Latest certified Intel v3 snapshot (pure DB read — zero LLM calls).
    service = IntelV3Service(user_id=user.id)
    snapshot = await service.get_latest_snapshot()

    # 2. Tax lots from transaction history (for profit/tax components).
    tx_result = (
        client.table("transactions")
        .select("ticker,tx_type,quantity,price,tx_date")
        .eq("user_id", str(user.id))
        .order("tx_date")
        .limit(10_000)
        .execute()
    )
    lots_by_ticker = build_tax_lots(
        tx_result.data or [],
        long_term_days=settings.long_term_holding_days,
    )

    # 3. Live prices for unrealized gain / tax estimates (degrade gracefully).
    prices: dict[str, float] = {}
    if lots_by_ticker:
        try:
            from ..services.price_engine import PriceService

            ps = PriceService(
                finnhub_key=settings.finnhub_api_key or "",
                alpaca_key=settings.alpaca_api_key or "",
                alpaca_secret=settings.alpaca_secret_key or "",
                polygon_key=settings.polygon_api_key or "",
            )
            price_results = await ps.fetch_prices(list(lots_by_ticker.keys()))
            for t, pr in price_results.items():
                if pr.is_valid:
                    prices[t] = pr.mid_price
        except Exception:
            pass

    enriched_lots = enrich_lots_with_market(
        lots_by_ticker,
        prices,
        short_term_rate=settings.tax_rate_short_term,
        long_term_rate=settings.tax_rate_long_term,
    )
    summaries = {t: summarize_ticker_lots(lots) for t, lots in enriched_lots.items()}

    # 4. User-defined target allocations (for allocation drift), if any.
    target_weights: dict[str, float] = {}
    try:
        ta = (
            client.table("target_allocations")
            .select("ticker,target_pct")
            .eq("user_id", str(user.id))
            .execute()
        )
        for row in ta.data or []:
            if row.get("ticker") and row.get("target_pct") is not None:
                target_weights[str(row["ticker"]).upper()] = float(row["target_pct"])
    except Exception:
        pass  # No targets → drift component simply absent

    return build_recommendation_panel(
        snapshot_payload=snapshot,
        lots_by_ticker=enriched_lots,
        lot_summaries=summaries,
        target_weights_pct=target_weights,
        profit_threshold_pct=settings.profit_taking_threshold_pct,
    )
