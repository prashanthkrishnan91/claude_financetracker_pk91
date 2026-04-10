"""DRIP service — dividend reinvestment analytics."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from ..database import get_supabase_client
from ..models.drip import DripHistoryEntry, DripPosition, DripSummary
from .recommendation_engine import DRIP_YIELD

# Ticker display names (best-effort; defaults to ticker if unknown)
_TICKER_NAMES: dict[str, str] = {
    "VYM": "Vanguard High Dividend Yield ETF",
    "SCHD": "Schwab U.S. Dividend Equity ETF",
    "BND": "Vanguard Total Bond Market ETF",
    "VWO": "Vanguard FTSE Emerging Markets ETF",
    "VXUS": "Vanguard Total Intl Stock ETF",
    "VEA": "Vanguard FTSE Developed Markets ETF",
    "XLE": "Energy Select Sector SPDR",
    "VTV": "Vanguard Value ETF",
    "VUG": "Vanguard Growth ETF",
    "SPY": "SPDR S&P 500 ETF",
    "VGT": "Vanguard Info Technology ETF",
    "VHT": "Vanguard Health Care ETF",
    "VIS": "Vanguard Industrials ETF",
    "VTI": "Vanguard Total Stock Market ETF",
    "VOO": "Vanguard S&P 500 ETF",
    "QQQ": "Invesco QQQ Trust",
    "QCOM": "Qualcomm Inc",
    "AAPL": "Apple Inc",
    "MSFT": "Microsoft Corp",
    "META": "Meta Platforms",
    "COST": "Costco Wholesale",
    "WMT": "Walmart Inc",
    "GOOGL": "Alphabet Inc",
    "TSM": "Taiwan Semiconductor",
    "NVDA": "NVIDIA Corp",
    "BRK-B": "Berkshire Hathaway B",
}


class DripService:
    """DRIP analytics — projections, positions, and dividend history."""

    def __init__(self, user_id: UUID, price_service=None) -> None:
        self.user_id = user_id
        self.client = get_supabase_client()
        self._price_service = price_service

    async def get_summary(self) -> DripSummary:
        """Return high-level DRIP analytics for the user's portfolio."""
        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        # Batch fetch prices once if possible
        prices: dict[str, float] = {}
        drip_tickers = [
            p["ticker"] for p in positions
            if DRIP_YIELD.get(p["ticker"], 0) > 0
        ]

        if drip_tickers and self._price_service:
            try:
                price_results = await self._price_service.fetch_prices(drip_tickers)
                for ticker, pr in price_results.items():
                    if pr.is_valid:
                        prices[ticker] = pr.mid_price
            except Exception:
                pass

        # lifetime_earned = sum of divs_received across all positions
        lifetime_earned = sum(
            float(p.get("divs_received") or 0) for p in positions
        )

        annual_projection = 0.0
        top_earner: Optional[str] = None
        top_income = 0.0
        positions_with_drip = 0

        for p in positions:
            ticker = p["ticker"]
            yld = DRIP_YIELD.get(ticker, 0)
            if yld <= 0:
                continue

            positions_with_drip += 1
            shares = float(p.get("shares") or 0)
            price = prices.get(ticker) or float(p.get("avg_cost") or 0)
            annual_income = shares * price * yld / 100
            annual_projection += annual_income

            if annual_income > top_income:
                top_income = annual_income
                top_earner = ticker

        return DripSummary(
            lifetime_earned=round(lifetime_earned, 2),
            annual_projection=round(annual_projection, 2),
            monthly_estimate=round(annual_projection / 12, 2),
            top_earner=top_earner,
            positions_with_drip=positions_with_drip,
        )

    async def get_positions(self) -> list[DripPosition]:
        """Return per-position DRIP details, sorted by annual income desc."""
        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        drip_positions = [
            p for p in positions if DRIP_YIELD.get(p["ticker"], 0) > 0
        ]

        # Batch-fetch live prices
        prices: dict[str, float] = {}
        if drip_positions and self._price_service:
            tickers = [p["ticker"] for p in drip_positions]
            try:
                price_results = await self._price_service.fetch_prices(tickers)
                for ticker, pr in price_results.items():
                    if pr.is_valid:
                        prices[ticker] = pr.mid_price
            except Exception:
                pass

        result_list: list[DripPosition] = []

        for p in drip_positions:
            ticker = p["ticker"]
            yld = DRIP_YIELD.get(ticker, 0)
            shares = float(p.get("shares") or 0)
            drip_shares = float(p.get("drip_shares") or 0)
            drip_cost = float(p.get("drip_cost") or 0)
            price = prices.get(ticker) or float(p.get("avg_cost") or 0)

            drip_value = drip_shares * price
            drip_gain = drip_value - drip_cost
            annual_income = shares * price * yld / 100

            result_list.append(
                DripPosition(
                    ticker=ticker,
                    name=_TICKER_NAMES.get(ticker, ticker),
                    shares=shares,
                    drip_shares=drip_shares,
                    drip_cost=round(drip_cost, 4),
                    drip_value=round(drip_value, 4),
                    drip_gain=round(drip_gain, 4),
                    annual_income=round(annual_income, 2),
                    yield_pct=yld,
                    ex_date=None,
                    pay_date=None,
                    category=p.get("category", ""),
                )
            )

        result_list.sort(key=lambda x: x.annual_income, reverse=True)
        return result_list

    async def get_history(self) -> list[DripHistoryEntry]:
        """Return dividend/DRIP transaction history (up to 200 rows)."""
        result = (
            self.client.table("transactions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .eq("tx_type", "CDIV")
            .order("tx_date", desc=True)
            .limit(200)
            .execute()
        )
        rows = result.data or []

        entries: list[DripHistoryEntry] = []
        for row in rows:
            entries.append(
                DripHistoryEntry(
                    id=str(row["id"]),
                    ticker=row.get("ticker"),
                    amount=float(row.get("amount") or 0),
                    tx_date=str(row.get("tx_date", "")),
                    description=row.get("description"),
                )
            )
        return entries
