"""
portfolio_aggregator.py — Portfolio War Room v11.0
Combines Plaid holdings (quantity) + real-time PriceService (mid_price)
to compute Total Equity matching Robinhood's Mark Price calculation.

Architecture:
    PlaidClient          → authoritative quantity per ticker
    PriceService         → real-time mid_price (bid+ask)/2 per ticker
    PortfolioAggregator  → multiplies them, calculates P&L, returns snapshot
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from plaid_client import PlaidClient, PlaidHolding, PlaidPortfolio
from price_service import PriceService, PriceResult

logger = logging.getLogger(__name__)


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class PositionSnapshot:
    """Single position — quantity from Plaid × price from PriceService."""
    ticker: str
    name: str
    quantity: float
    avg_cost_basis: float     # Per share (from Plaid)
    mid_price: float          # Real-time mark price
    market_value: float       # quantity × mid_price
    cost_total: float         # quantity × avg_cost_basis
    unrealised_pnl: float     # market_value − cost_total
    unrealised_pct: float     # unrealised_pnl / cost_total × 100
    price_source: str         # 'finnhub' | 'polygon' | 'coingecko' | 'cache(...)'
    bid: Optional[float]
    ask: Optional[float]
    last_trade: float
    security_type: str
    price_stale: bool         # True if using cached / fallback price
    price_error: Optional[str]


@dataclass
class PortfolioSnapshot:
    """
    Complete portfolio snapshot matching Robinhood's display:
        Total Equity = Stocks Equity + Crypto Equity + Cash
    """
    positions: list[PositionSnapshot] = field(default_factory=list)
    stocks_equity: float = 0.0      # All equity/ETF positions
    crypto_equity: float = 0.0      # Crypto positions
    cash_usd: float = 0.0           # Cash from Plaid
    total_equity: float = 0.0       # = stocks + crypto + cash
    total_cost_basis: float = 0.0
    total_unrealised_pnl: float = 0.0
    total_unrealised_pct: float = 0.0
    positions_count: int = 0
    stale_prices: list[str] = field(default_factory=list)   # Tickers with stale prices
    failed_prices: list[str] = field(default_factory=list)  # Tickers that failed entirely
    snapshot_timestamp: float = field(default_factory=time.time)
    plaid_account_ids: list[str] = field(default_factory=list)


# ─── PortfolioAggregator ──────────────────────────────────────────────────────

class PortfolioAggregator:
    """
    Orchestrates Plaid + PriceService to produce a PortfolioSnapshot.

    Usage:
        aggregator = PortfolioAggregator()
        snapshot = aggregator.sync_portfolio_total()
        print(f"Total Equity: ${snapshot.total_equity:,.2f}")
    """

    def __init__(
        self,
        plaid_client: Optional[PlaidClient] = None,
        price_service: Optional[PriceService] = None,
    ) -> None:
        self._plaid   = plaid_client  or PlaidClient()
        self._prices  = price_service or PriceService()

    # ── Main sync function ────────────────────────────────────────────────────

    def sync_portfolio_total(
        self,
        account_ids: Optional[list[str]] = None,
    ) -> PortfolioSnapshot:
        """
        Fetch holdings from Plaid and real-time prices concurrently.
        Returns a PortfolioSnapshot with Total Equity matching Robinhood's Mark Price.

        Steps:
            1. Fetch Plaid holdings (blocking — sequential, usually <1s)
            2. Extract all tickers
            3. Fetch all prices concurrently via ThreadPoolExecutor
            4. Multiply quantity × mid_price for each position
            5. Sum into buckets: stocks + crypto + cash

        Args:
            account_ids: Optional Plaid account filter.
        """
        t0 = time.monotonic()

        # ── Step 1: Plaid holdings ────────────────────────────────────────────
        logger.info("Fetching Plaid holdings…")
        portfolio: PlaidPortfolio = self._plaid.get_holdings(account_ids=account_ids)
        logger.info("Plaid returned %d holdings", len(portfolio.holdings))

        if not portfolio.holdings:
            logger.warning("No holdings returned from Plaid — returning empty snapshot")
            return PortfolioSnapshot(
                cash_usd=portfolio.cash_usd,
                total_equity=portfolio.cash_usd,
                snapshot_timestamp=time.time(),
                plaid_account_ids=portfolio.account_ids,
            )

        # ── Step 2: Extract unique tickers ────────────────────────────────────
        tickers = list({h.ticker for h in portfolio.holdings if h.ticker})
        logger.info("Fetching real-time prices for %d tickers…", len(tickers))

        # ── Step 3: Concurrent price fetch ───────────────────────────────────
        price_map: dict[str, PriceResult] = self._prices.fetch_prices(tickers)

        t1 = time.monotonic()
        logger.info("Price fetch completed in %.2fs", t1 - t0)

        # ── Step 4: Build positions ───────────────────────────────────────────
        positions: list[PositionSnapshot] = []
        stale_tickers: list[str] = []
        failed_tickers: list[str] = []

        for holding in portfolio.holdings:
            pos = self._build_position(holding, price_map)
            positions.append(pos)

            if pos.price_stale:
                stale_tickers.append(pos.ticker)
            if pos.price_error and not pos.price_stale:
                failed_tickers.append(pos.ticker)

        # ── Step 5: Aggregate totals ──────────────────────────────────────────
        snapshot = self._aggregate(
            positions=positions,
            cash_usd=portfolio.cash_usd,
            stale=stale_tickers,
            failed=failed_tickers,
            account_ids=portfolio.account_ids,
        )

        logger.info(
            "Portfolio sync complete | Total: $%.2f | Stocks: $%.2f | "
            "Crypto: $%.2f | Cash: $%.2f | %.2fs elapsed",
            snapshot.total_equity,
            snapshot.stocks_equity,
            snapshot.crypto_equity,
            snapshot.cash_usd,
            time.monotonic() - t0,
        )

        return snapshot

    async def sync_portfolio_total_async(
        self,
        account_ids: Optional[list[str]] = None,
    ) -> PortfolioSnapshot:
        """Async version — runs sync_portfolio_total in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.sync_portfolio_total, account_ids
        )

    # ── Position builder ──────────────────────────────────────────────────────

    def _build_position(
        self,
        holding: PlaidHolding,
        price_map: dict[str, PriceResult],
    ) -> PositionSnapshot:
        """Build one PositionSnapshot by combining Plaid qty + real-time price."""
        price_result = price_map.get(holding.ticker)

        if price_result is None:
            # ticker wasn't in our fetch batch (shouldn't happen)
            mid_price   = holding.institution_price  # Plaid's fallback
            price_src   = "institution_fallback"
            is_stale    = True
            price_error = "Not in price fetch batch"
            bid = ask = None
            last_trade  = mid_price
        elif not price_result.is_valid:
            # All providers failed — use Plaid institution price as last resort
            mid_price   = holding.institution_price or 0.0
            price_src   = "institution_fallback"
            is_stale    = True
            price_error = price_result.error
            bid = ask = None
            last_trade  = mid_price
        else:
            mid_price   = price_result.mid_price
            price_src   = price_result.source
            is_stale    = price_result.error is not None   # has error but is_valid = cached
            price_error = price_result.error
            bid         = price_result.bid
            ask         = price_result.ask
            last_trade  = price_result.last_trade

        qty          = holding.quantity
        cost_per_sh  = holding.cost_basis
        market_val   = qty * mid_price
        cost_total   = qty * cost_per_sh
        unreal_pnl   = market_val - cost_total
        unreal_pct   = (unreal_pnl / cost_total * 100) if cost_total > 0 else 0.0

        return PositionSnapshot(
            ticker=holding.ticker,
            name=holding.name,
            quantity=qty,
            avg_cost_basis=cost_per_sh,
            mid_price=mid_price,
            market_value=market_val,
            cost_total=cost_total,
            unrealised_pnl=unreal_pnl,
            unrealised_pct=unreal_pct,
            price_source=price_src,
            bid=bid,
            ask=ask,
            last_trade=last_trade,
            security_type=holding.security_type,
            price_stale=is_stale,
            price_error=price_error,
        )

    # ── Aggregation ───────────────────────────────────────────────────────────

    @staticmethod
    def _aggregate(
        positions: list[PositionSnapshot],
        cash_usd: float,
        stale: list[str],
        failed: list[str],
        account_ids: list[str],
    ) -> PortfolioSnapshot:
        """Sum positions into stocks / crypto / cash buckets."""
        _CRYPTO = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "AVAX", "MATIC", "DOT", "LTC"}

        stocks_eq = crypto_eq = cost_tot = 0.0

        for pos in positions:
            if pos.ticker.upper() in _CRYPTO:
                crypto_eq += pos.market_value
            else:
                stocks_eq += pos.market_value
            cost_tot += pos.cost_total

        total_eq  = stocks_eq + crypto_eq + cash_usd
        total_pnl = total_eq - cash_usd - cost_tot   # exclude cash from P&L calc
        total_pct = (total_pnl / cost_tot * 100) if cost_tot > 0 else 0.0

        return PortfolioSnapshot(
            positions=positions,
            stocks_equity=stocks_eq,
            crypto_equity=crypto_eq,
            cash_usd=cash_usd,
            total_equity=total_eq,
            total_cost_basis=cost_tot,
            total_unrealised_pnl=total_pnl,
            total_unrealised_pct=total_pct,
            positions_count=len(positions),
            stale_prices=stale,
            failed_prices=failed,
            snapshot_timestamp=time.time(),
            plaid_account_ids=account_ids,
        )
