"""
portfolio_aggregator.py — Portfolio War Room v11.1
Combines HoldingsManager (smart-cached Plaid quantities) +
async PriceService (Finnhub/Polygon/CoinGecko) to compute Total Equity.

Key changes vs v11.0:
  - PlaidClient no longer called directly — all holdings via HoldingsManager
  - HoldingsManager enforces 24h TTL: Plaid called at most once/day
  - calculate_total_value() is the new primary entry point
  - sync_portfolio_total() kept as backward-compat alias
  - Async path uses fully-async PriceService (asyncio.gather + aiohttp)
  - PortfolioSnapshot gains: holdings_cache_age_h, plaid_sync_triggered
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from holdings_manager import HoldingsManager, HoldingsCache, CachedHolding
from price_service import PriceService, PriceResult

logger = logging.getLogger(__name__)

_CRYPTO_TICKERS = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "AVAX", "MATIC", "DOT", "LTC"}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PositionSnapshot:
    """Single position — quantity from HoldingsManager × price from PriceService."""
    ticker:         str
    name:           str
    quantity:       float
    avg_cost_basis: float
    mid_price:      float
    market_value:   float
    cost_total:     float
    unrealised_pnl: float
    unrealised_pct: float
    price_source:   str
    bid:            Optional[float]
    ask:            Optional[float]
    last_trade:     float
    security_type:  str
    price_stale:    bool
    price_error:    Optional[str]


@dataclass
class PortfolioSnapshot:
    """
    Complete portfolio snapshot.
    Total Equity = Stocks + Crypto + Cash  (mirrors Robinhood's Mark Price total)
    """
    positions:            list[PositionSnapshot] = field(default_factory=list)
    stocks_equity:        float = 0.0
    crypto_equity:        float = 0.0
    cash_usd:             float = 0.0
    total_equity:         float = 0.0
    total_cost_basis:     float = 0.0
    total_unrealised_pnl: float = 0.0
    total_unrealised_pct: float = 0.0
    positions_count:      int   = 0
    stale_prices:         list[str] = field(default_factory=list)
    failed_prices:        list[str] = field(default_factory=list)
    snapshot_timestamp:   float = field(default_factory=time.time)
    plaid_account_ids:    list[str] = field(default_factory=list)
    # Smart-sync metadata
    holdings_cache_age_h: float = 0.0   # Age of holdings_cache.json in hours
    plaid_sync_triggered: bool  = False  # True if this run actually called Plaid


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO AGGREGATOR
# ═══════════════════════════════════════════════════════════════════════════════

class PortfolioAggregator:
    """
    Orchestrates HoldingsManager + PriceService to produce a PortfolioSnapshot.

    Smart Sync:
      Holdings quantity  → HoldingsCache (Plaid called only if cache >24h old)
      Live prices        → Finnhub/Polygon/CoinGecko (called every time, no Plaid)
      Fallback prices    → institution_price stored in HoldingsCache

    Quick start:
        agg  = PortfolioAggregator()
        snap = agg.calculate_total_value()
        print(f"${snap.total_equity:,.2f}")

        # Force Plaid re-sync
        snap = agg.calculate_total_value(force_plaid_refresh=True)

        # Async
        snap = await agg.calculate_total_value_async()
    """

    def __init__(
        self,
        holdings_manager: Optional[HoldingsManager] = None,
        price_service:    Optional[PriceService]    = None,
    ) -> None:
        self._holdings = holdings_manager or HoldingsManager()
        self._prices   = price_service    or PriceService()

    # ── Primary sync entry point ──────────────────────────────────────────────

    def calculate_total_value(
        self,
        force_plaid_refresh: bool = False,
        account_ids: Optional[list[str]] = None,
    ) -> PortfolioSnapshot:
        """
        Loads quantities from local cache (or Plaid if missing/stale),
        fetches fresh prices, returns PortfolioSnapshot.

        Plaid is called ONLY when:
          - holdings_cache.json is missing
          - Cache is older than 24 hours (HOLDINGS_CACHE_TTL_HOURS env var)
          - force_plaid_refresh=True

        Prices are always fetched live — independent of Plaid.
        """
        t0 = time.monotonic()
        needs_sync, reason = self._holdings.needs_plaid_sync(force_refresh=force_plaid_refresh)
        logger.info("Holdings [sync]: %s", reason)

        cache: HoldingsCache = self._holdings.get_holdings(
            force_refresh=force_plaid_refresh,
            account_ids=account_ids,
        )
        plaid_triggered = needs_sync

        if not cache.holdings:
            logger.warning("No holdings in cache — returning cash-only snapshot")
            return PortfolioSnapshot(
                cash_usd=cache.cash_usd, total_equity=cache.cash_usd,
                snapshot_timestamp=time.time(),
                plaid_account_ids=cache.account_ids,
                holdings_cache_age_h=round(cache.age_hours, 2),
                plaid_sync_triggered=plaid_triggered,
            )

        tickers = list({h.ticker for h in cache.holdings if h.ticker})
        logger.info("Fetching live prices for %d tickers…", len(tickers))

        price_map: dict[str, PriceResult] = self._prices.fetch_prices(
            tickers, institution_fallback=cache
        )
        logger.info("Price fetch done in %.2fs", time.monotonic() - t0)

        positions, stale, failed = [], [], []
        for holding in cache.holdings:
            pos = self._build_position(holding, price_map)
            positions.append(pos)
            if pos.price_stale:
                stale.append(pos.ticker)
            if pos.price_error and not pos.price_stale:
                failed.append(pos.ticker)

        snapshot = self._aggregate(
            positions=positions, cash_usd=cache.cash_usd,
            stale=stale, failed=failed, account_ids=cache.account_ids,
            cache_age_h=cache.age_hours, plaid_triggered=plaid_triggered,
        )
        logger.info(
            "Portfolio total | $%.2f | Plaid called: %s | %.2fs elapsed",
            snapshot.total_equity, plaid_triggered, time.monotonic() - t0,
        )
        return snapshot

    # ── Primary async entry point ─────────────────────────────────────────────

    async def calculate_total_value_async(
        self,
        force_plaid_refresh: bool = False,
        account_ids: Optional[list[str]] = None,
    ) -> PortfolioSnapshot:
        """
        Async version — holdings loaded in executor (Plaid SDK is sync),
        prices fetched via asyncio.gather + aiohttp in parallel.
        """
        t0 = time.monotonic()
        needs_sync, reason = self._holdings.needs_plaid_sync(force_refresh=force_plaid_refresh)
        logger.info("Holdings [async]: %s", reason)

        loop  = asyncio.get_event_loop()
        cache = await loop.run_in_executor(
            None,
            lambda: self._holdings.get_holdings(
                force_refresh=force_plaid_refresh, account_ids=account_ids
            ),
        )
        plaid_triggered = needs_sync

        if not cache.holdings:
            return PortfolioSnapshot(
                cash_usd=cache.cash_usd, total_equity=cache.cash_usd,
                snapshot_timestamp=time.time(), plaid_account_ids=cache.account_ids,
                holdings_cache_age_h=round(cache.age_hours, 2),
                plaid_sync_triggered=plaid_triggered,
            )

        tickers   = list({h.ticker for h in cache.holdings if h.ticker})
        price_map = await self._prices.fetch_prices_async(tickers, institution_fallback=cache)

        positions, stale, failed = [], [], []
        for holding in cache.holdings:
            pos = self._build_position(holding, price_map)
            positions.append(pos)
            if pos.price_stale:
                stale.append(pos.ticker)
            if pos.price_error and not pos.price_stale:
                failed.append(pos.ticker)

        snapshot = self._aggregate(
            positions=positions, cash_usd=cache.cash_usd,
            stale=stale, failed=failed, account_ids=cache.account_ids,
            cache_age_h=cache.age_hours, plaid_triggered=plaid_triggered,
        )
        logger.info("Async total | $%.2f | %.2fs", snapshot.total_equity, time.monotonic() - t0)
        return snapshot

    # ── Backward-compatibility aliases ────────────────────────────────────────

    def sync_portfolio_total(
        self,
        account_ids: Optional[list[str]] = None,
        force_refresh: bool = False,
    ) -> PortfolioSnapshot:
        """Alias — preserves v11.0 call sites in data_engine.py / main_sync.py."""
        return self.calculate_total_value(
            force_plaid_refresh=force_refresh, account_ids=account_ids
        )

    async def sync_portfolio_total_async(
        self, account_ids: Optional[list[str]] = None
    ) -> PortfolioSnapshot:
        return await self.calculate_total_value_async(account_ids=account_ids)

    # ── Position builder ──────────────────────────────────────────────────────

    def _build_position(
        self, holding: CachedHolding, price_map: dict[str, PriceResult]
    ) -> PositionSnapshot:
        pr = price_map.get(holding.ticker)

        if pr is None or not pr.is_valid:
            mid      = holding.institution_price or 0.0
            src      = "institution_fallback"
            is_stale = True
            err      = pr.error if pr else "Not in price fetch batch"
            bid = ask = None
            last     = mid
        else:
            mid      = pr.mid_price
            src      = pr.source
            is_stale = pr.is_stale
            err      = pr.error
            bid      = pr.bid
            ask      = pr.ask
            last     = pr.last_trade

        qty        = holding.quantity
        cost_sh    = holding.cost_basis
        mkt_val    = qty * mid
        cost_tot   = qty * cost_sh
        unreal     = mkt_val - cost_tot
        unreal_pct = (unreal / cost_tot * 100) if cost_tot > 0 else 0.0

        return PositionSnapshot(
            ticker=holding.ticker, name=holding.name,
            quantity=qty, avg_cost_basis=cost_sh,
            mid_price=mid, market_value=mkt_val,
            cost_total=cost_tot, unrealised_pnl=unreal,
            unrealised_pct=unreal_pct, price_source=src,
            bid=bid, ask=ask, last_trade=last,
            security_type=holding.security_type,
            price_stale=is_stale, price_error=err,
        )

    # ── Aggregation ───────────────────────────────────────────────────────────

    @staticmethod
    def _aggregate(
        positions: list[PositionSnapshot], cash_usd: float,
        stale: list[str], failed: list[str], account_ids: list[str],
        cache_age_h: float, plaid_triggered: bool,
    ) -> PortfolioSnapshot:
        stocks_eq = crypto_eq = cost_tot = 0.0
        for pos in positions:
            if pos.ticker.upper() in _CRYPTO_TICKERS:
                crypto_eq += pos.market_value
            else:
                stocks_eq += pos.market_value
            cost_tot += pos.cost_total

        total_eq  = stocks_eq + crypto_eq + cash_usd
        total_pnl = (stocks_eq + crypto_eq) - cost_tot
        total_pct = (total_pnl / cost_tot * 100) if cost_tot > 0 else 0.0

        return PortfolioSnapshot(
            positions=positions,
            stocks_equity=stocks_eq, crypto_equity=crypto_eq,
            cash_usd=cash_usd, total_equity=total_eq,
            total_cost_basis=cost_tot,
            total_unrealised_pnl=total_pnl, total_unrealised_pct=total_pct,
            positions_count=len(positions),
            stale_prices=stale, failed_prices=failed,
            snapshot_timestamp=time.time(),
            plaid_account_ids=account_ids,
            holdings_cache_age_h=round(cache_age_h, 2),
            plaid_sync_triggered=plaid_triggered,
        )
