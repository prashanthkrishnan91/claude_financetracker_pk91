"""Build 3 PR 2B — PriceBand visible context integration for the v3 snapshot.

Async integration module that:
  1. Fetches source-linked FY EPS from research_artifact_facts.
  2. Fetches fresh price + sector from market_snapshots.
  3. Calls Phase 14D (build_priceband_shadow) for each company ticker.
  4. Calls Phase 14F (build_visible_context) to get plain-English context.
  5. Returns a ticker→serialized-context map for snapshot_builder consumption.

Serialized context placed in detail_drawer_payload.valuation_context:
  - {"visible_text": "...", "limitation_text": "...", "source_basis": "..."} when renderable
  - None when unavailable / negative_eps / low confidence / ETF / disabled

Contract invariants:
  - Never blocks snapshot build — all errors return empty map.
  - Never emits target_price, fair_value, intrinsic_value, upside, downside.
  - Never derives valuation context from action, conviction, or evidence_band.
  - Non-company tickers (ETFs, crypto) always receive None context.
  - Price stale threshold: PRICE_STALE_THRESHOLD_DAYS = 7 days (matches Phase 14B/14C).
  - Config flag: intel_v3_priceband_visible_context_v1_enabled (defaults False).
  - No LLM. No provider calls beyond market_snapshots + research_artifact_facts reads.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

PRICE_STALE_THRESHOLD_DAYS: int = 7

_EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")

# Non-company asset categories — valuation ratio logic must not apply.
_NON_COMPANY_CATEGORIES: frozenset[str] = frozenset({
    "etf", "fund", "crypto", "bond", "commodity", "reit_external",
})


async def build_ticker_valuation_context_map(
    *,
    user_id: UUID,
    client: Any,
    tickers: list[str],
    categories: dict[str, str],
) -> dict[str, Optional[dict]]:
    """Build a ticker-to-serialized-valuation-context map.

    Args:
        user_id:    The authenticated user's UUID.
        client:     Supabase client.
        tickers:    Active company ticker list (upper-case expected).
        categories: Mapping of ticker → category string for ETF/fund suppression.

    Returns:
        dict[str, Optional[dict]] — ticker → serialized context or None.
        - Renderable: {"visible_text": "...", "limitation_text": "...", "source_basis": "..."}
        - Suppressed / unavailable: None
        Errors within DB fetches degrade gracefully — affected tickers receive None.
    """
    if not tickers:
        return {}

    try:
        return await _build(
            user_id=user_id,
            client=client,
            tickers=tickers,
            categories=categories,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "priceband_snapshot_context.build_failed user_id=%s error=%s — "
            "snapshot proceeds with no valuation context",
            user_id, exc,
        )
        return {}


# ── Serialization ─────────────────────────────────────────────────────────────

def _serialize_context(ctx: Any) -> Optional[dict]:
    """Serialize a PriceBandVisibleContext to a snapshot-safe dict.

    Returns None when should_render is False (suppressed/unavailable).
    The serialized dict never contains raw metric keys, internal enum values,
    target prices, fair values, or numeric financial metrics.
    """
    if ctx is None or not ctx.should_render:
        return None
    return {
        "visible_text": ctx.visible_text,
        "limitation_text": ctx.limitation_text,
        "source_basis": ctx.source_basis,
    }


# ── Main build ────────────────────────────────────────────────────────────────

async def _build(
    *,
    user_id: UUID,
    client: Any,
    tickers: list[str],
    categories: dict[str, str],
) -> dict[str, Optional[dict]]:
    from .eps_payload_extractor_v1 import extract_fy_eps_observation_from_payload
    from .priceband_shadow_policy_v1 import PriceBandShadowInput, build_priceband_shadow
    from .priceband_visible_context_v1 import build_visible_context

    # Separate company tickers from non-company (ETF/fund/crypto) — skip non-company.
    company_tickers = [
        t for t in tickers
        if categories.get(t, "stock").lower() not in _NON_COMPANY_CATEGORIES
    ]

    if not company_tickers:
        logger.info(
            "valuation_context_pr2b_aggregate_summary user_id=%s "
            "flag_enabled=true "
            "total_tickers=%d company_ticker_count=0 non_company_suppressed_count=%d "
            "eps_found_count=0 source_linked_eps_count=0 "
            "fresh_price_count=0 sector_found_count=0 "
            "priceband_computed_count=0 "
            "renderable_context_count=0 suppressed_context_count=%d "
            "suppression_missing_eps=0 suppression_stale_price=0 "
            "suppression_missing_price=0 suppression_zero_eps=0 "
            "suppression_non_positive_price=0 "
            "fetch_errors=0",
            user_id, len(tickers), len(tickers), len(tickers),
        )
        return {t: None for t in tickers}

    user_id_str = str(user_id)
    errors: list[str] = []

    # ── Step 1: FY EPS from research_artifact_facts ───────────────────────────
    fy_diluted_by_ticker: dict[str, tuple[int, float]] = {}
    fy_basic_by_ticker: dict[str, tuple[int, float]] = {}
    eps_source_linked_tickers: set[str] = set()

    try:
        art_result = await asyncio.to_thread(
            lambda: client.table("research_artifacts")
            .select("id,ticker")
            .eq("user_id", user_id_str)
            .in_("ticker", company_tickers)
            .execute()
        )
        ticker_by_artifact_id: dict[str, str] = {
            str(row["id"]): str(row.get("ticker") or "").upper().strip()
            for row in (art_result.data or [])
            if row.get("id") and row.get("ticker")
        }
        artifact_ids = list(ticker_by_artifact_id.keys())

        if artifact_ids:
            fact_result = await asyncio.to_thread(
                lambda: client.table("research_artifact_facts")
                .select("artifact_id,fact_kind,structured_payload,source_id")
                .eq("user_id", user_id_str)
                .in_("artifact_id", artifact_ids)
                .execute()
            )
            for row in (fact_result.data or []):
                if str(row.get("fact_kind") or "") != "metric_observation":
                    continue
                sp = row.get("structured_payload")
                if not isinstance(sp, dict) or sp.get("claim") != "sec_companyfact_observed":
                    continue
                if str(sp.get("tag") or "") not in _EPS_TAGS:
                    continue
                aid = str(row.get("artifact_id") or "")
                ticker = ticker_by_artifact_id.get(aid, "")
                if not ticker or ticker not in set(company_tickers):
                    continue
                has_source = bool(row.get("source_id") and str(row.get("source_id")).strip())
                extraction = extract_fy_eps_observation_from_payload(sp, has_source=has_source)
                if extraction.skip_reason:
                    continue
                eps_source_linked_tickers.add(ticker)
                target = (
                    fy_diluted_by_ticker
                    if extraction.tag == "EarningsPerShareDiluted"
                    else fy_basic_by_ticker
                )
                cur = target.get(ticker)
                if cur is None or extraction.ordering_year > cur[0]:
                    target[ticker] = (extraction.ordering_year, extraction.eps_value)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eps_fetch_error: {exc}")
        logger.warning(
            "priceband_snapshot_context.eps_fetch_failed user_id=%s error=%s",
            user_id, exc,
        )

    # ── Step 2: Fresh price + sector/industry from market_snapshots ───────────
    cutoff_date = (
        datetime.now(timezone.utc) - timedelta(days=PRICE_STALE_THRESHOLD_DAYS)
    ).strftime("%Y-%m-%d")
    price_by_ticker: dict[str, tuple[float, bool]] = {}
    sector_label_by_ticker: dict[str, str] = {}
    industry_label_by_ticker: dict[str, str] = {}

    try:
        ms_result = await asyncio.to_thread(
            lambda: client.table("market_snapshots")
            .select("ticker,as_of,price,sector,industry")
            .eq("user_id", user_id_str)
            .in_("ticker", company_tickers)
            .order("as_of", desc=True)
            .execute()
        )
        seen: set[str] = set()
        for row in (ms_result.data or []):
            t = str(row.get("ticker") or "").upper().strip()
            if not t or t not in set(company_tickers) or t in seen:
                continue
            seen.add(t)
            as_of = str(row.get("as_of") or "")
            price_val = row.get("price")
            if price_val is not None and as_of:
                try:
                    p = float(price_val)
                    is_fresh = as_of[:10] >= cutoff_date
                    price_by_ticker[t] = (p, is_fresh)
                except (TypeError, ValueError):
                    pass
            sector_val = str(row.get("sector") or "").strip()
            industry_val = str(row.get("industry") or "").strip()
            if sector_val:
                sector_label_by_ticker[t] = sector_val
            if industry_val:
                industry_label_by_ticker[t] = industry_val
    except Exception as exc:  # noqa: BLE001
        errors.append(f"market_snapshots_fetch_error: {exc}")
        logger.warning(
            "priceband_snapshot_context.price_fetch_failed user_id=%s error=%s",
            user_id, exc,
        )

    # Intermediate counts for aggregate observability log (PR 2B root-cause fix).
    eps_found_count = sum(
        1 for t in company_tickers
        if fy_diluted_by_ticker.get(t) is not None or fy_basic_by_ticker.get(t) is not None
    )
    fresh_price_count = sum(1 for (_, is_fresh) in price_by_ticker.values() if is_fresh)
    sector_found_count = len(sector_label_by_ticker)

    # ── Step 3: Build PriceBandShadowInput records ────────────────────────────
    company_set = set(company_tickers)
    records: list[PriceBandShadowInput] = []
    for ticker in company_tickers:
        diluted = fy_diluted_by_ticker.get(ticker)
        basic = fy_basic_by_ticker.get(ticker)
        price_entry = price_by_ticker.get(ticker)
        sector_label = sector_label_by_ticker.get(ticker)
        industry_label = industry_label_by_ticker.get(ticker)
        records.append(PriceBandShadowInput(
            ticker=ticker,
            fy_diluted_eps=(diluted[1] if diluted is not None else None),
            fy_basic_eps=(basic[1] if basic is not None else None),
            eps_source_linked=(ticker in eps_source_linked_tickers),
            price=(price_entry[0] if price_entry is not None else None),
            price_fresh=(price_entry[1] if price_entry is not None else False),
            sector_available=bool(sector_label),
            industry_available=bool(industry_label),
            sector_label=sector_label,
            industry_label=industry_label,
        ))

    # ── Step 4: Phase 14D shadow classification ───────────────────────────────
    from .priceband_shadow_policy_v1 import (
        REASON_MISSING_EPS, REASON_STALE_PRICE, REASON_MISSING_PRICE,
        REASON_ZERO_EPS, REASON_NON_POSITIVE_PRICE,
    )
    shadow_result = build_priceband_shadow(records=records, extra_errors=errors)

    computed_count = shadow_result.priceband_computed_count
    unavailable_count = shadow_result.priceband_unavailable_count
    logger.info(
        "priceband_snapshot_context.classified user_id=%s "
        "company_tickers=%d priceband_computed=%d priceband_unavailable=%d errors=%d",
        user_id,
        len(company_tickers),
        computed_count,
        unavailable_count,
        len(errors),
    )

    # ── Step 5: Phase 14F visible context per ticker ──────────────────────────
    diag_by_ticker: dict[str, Any] = {
        d.ticker: d for d in shadow_result.priceband_diagnostics
    }
    # enabled=True because the caller (intel_v3_service) only calls this when the
    # config flag is True. The flag gate is enforced in intel_v3_service, not here.
    context_map: dict[str, Optional[dict]] = {}

    for ticker in tickers:
        if ticker not in company_set:
            # Non-company: ETF/fund/crypto — always suppress
            context_map[ticker] = None
            continue
        diag = diag_by_ticker.get(ticker)
        if diag is None:
            context_map[ticker] = None
            continue
        ctx = build_visible_context(enabled=True, diagnostic=diag)
        context_map[ticker] = _serialize_context(ctx)

    renderable = sum(1 for v in context_map.values() if v is not None)
    logger.info(
        "priceband_snapshot_context.serialized user_id=%s "
        "total_tickers=%d renderable=%d suppressed=%d",
        user_id,
        len(tickers),
        renderable,
        len(tickers) - renderable,
    )

    # ── Aggregate production-safe observability log (PR 2B root-cause fix) ───
    # Emitted every time the bridge runs so Railway logs can explain exactly
    # why valuation context is or is not rendered per snapshot build.
    # No raw EPS values, prices, ratios, or per-ticker sensitive data.
    non_company_suppressed_count = len(tickers) - len(company_tickers)
    unavail = shadow_result.unavailable_reason_counts
    logger.info(
        "valuation_context_pr2b_aggregate_summary user_id=%s "
        "flag_enabled=true "
        "total_tickers=%d company_ticker_count=%d non_company_suppressed_count=%d "
        "eps_found_count=%d source_linked_eps_count=%d "
        "fresh_price_count=%d sector_found_count=%d "
        "priceband_computed_count=%d "
        "renderable_context_count=%d suppressed_context_count=%d "
        "suppression_missing_eps=%d suppression_stale_price=%d "
        "suppression_missing_price=%d suppression_zero_eps=%d "
        "suppression_non_positive_price=%d "
        "fetch_errors=%d",
        user_id,
        len(tickers), len(company_tickers), non_company_suppressed_count,
        eps_found_count, len(eps_source_linked_tickers),
        fresh_price_count, sector_found_count,
        shadow_result.priceband_computed_count,
        renderable, len(tickers) - renderable,
        unavail.get(REASON_MISSING_EPS, 0),
        unavail.get(REASON_STALE_PRICE, 0),
        unavail.get(REASON_MISSING_PRICE, 0),
        unavail.get(REASON_ZERO_EPS, 0),
        unavail.get(REASON_NON_POSITIVE_PRICE, 0),
        len(errors),
    )

    return context_map
