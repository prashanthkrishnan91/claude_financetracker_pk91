"""Portfolio service — summary computation, snapshots, rebalancing.

Integrates with the v2 concurrent price engine for live portfolio values.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

from ..config import get_settings
from ..database import get_supabase_client
from ..models.portfolio import (
    PortfolioSummary,
    RebalanceResult,
    SnapshotResponse,
    TargetAllocationCreate,
    TargetAllocationResponse,
)


# ── Deposit formula constants ─────────────────────────────────────────────────

_DEPOSIT_FORMULA = [
    ("NVDA",     28.0),
    ("VOO",      22.0),
    ("VYM",      17.0),
    ("QQQ",      17.0),
    ("ROTATING", 16.0),
]

_FORMULA_RATIONALE: dict[str, str] = {
    "NVDA": "AI/GPU infrastructure leader — high-conviction core holding",
    "VOO":  "S&P 500 index core — DCA every deposit, never sell",
    "VYM":  "High-yield dividend ETF — income + DRIP compounding, hold forever",
    "QQQ":  "NASDAQ-100 — tech and growth index exposure",
}

# Annual dividend yield estimates (used as DRIP fallback when no Intel drip_note)
_DRIP_YIELD_MAP: dict[str, float] = {
    "VYM": 2.8, "SCHD": 3.5, "BND": 3.2, "VWO": 2.1, "VXUS": 1.8,
    "VOO": 1.2, "QQQ": 0.5, "VTI": 1.3, "SPY": 1.2, "QCOM": 2.1,
    "AAPL": 0.5, "MSFT": 0.7, "META": 0.4, "COST": 0.7, "WMT": 0.9,
    "NVDA": 0.1, "TSM": 1.4, "BMWYY": 4.5,
}


class PortfolioService:
    """All portfolio-level business logic."""

    def __init__(self, user_id: UUID, price_service=None):
        self.user_id = user_id
        self.client = get_supabase_client()
        self._price_service = price_service

    def _get_price_service(self):
        """Lazily create PriceService from settings if not provided."""
        if self._price_service is None:
            from ..services.price_engine import PriceService
            settings = get_settings()
            self._price_service = PriceService(
                finnhub_key=settings.finnhub_api_key or "",
                alpaca_key=settings.alpaca_api_key or "",
                alpaca_secret=settings.alpaca_secret_key or "",
                polygon_key=settings.polygon_api_key or "",
            )
        return self._price_service

    async def get_summary(self) -> PortfolioSummary:
        """Compute portfolio summary with live prices.

        Uses the concurrent price engine for real-time data.
        Also computes day_change from price_history previous-close data.
        Cash balance prefers users.cash_override over Plaid sync log.
        """
        # Fetch all positions
        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data

        if not positions:
            return PortfolioSummary(
                total_equity=0, total_cost=0, total_pnl=0, total_pnl_pct=0,
                cash_balance=0, day_change=0, day_change_pct=0,
                stocks_value=0, etfs_value=0, crypto_value=0,
                positions_count=0, prices_fresh=0, prices_stale=0,
            )

        # Fetch live prices — use lazy-init price service
        price_service = self._get_price_service()
        prices: dict[str, float] = {}
        fresh_count = 0
        stale_count = 0
        sources_used: set[str] = set()

        tickers = [p["ticker"] for p in positions]
        try:
            price_results = await price_service.fetch_prices(tickers)
            for ticker, pr in price_results.items():
                if pr.is_valid:
                    prices[ticker] = pr.mid_price
                    if not pr.is_stale:
                        fresh_count += 1
                        sources_used.add(pr.source.split("(")[0])
                    else:
                        stale_count += 1
                else:
                    stale_count += 1
        except Exception:
            stale_count = len(positions)

        # Compute portfolio values
        total_equity = 0.0
        total_cost = 0.0
        stocks_value = 0.0
        etfs_value = 0.0
        crypto_value = 0.0

        for p in positions:
            shares = float(p["shares"])
            avg_cost = float(p["avg_cost"])
            cost = shares * avg_cost
            total_cost += cost

            price = prices.get(p["ticker"])
            if price:
                market_value = shares * price
            else:
                market_value = cost  # Use cost as fallback

            total_equity += market_value

            cat = p["category"]
            if cat in ("Core", "Other", "IPO", "SELL"):
                stocks_value += market_value
            elif cat == "ETF":
                etfs_value += market_value
            elif cat == "Crypto":
                crypto_value += market_value

        total_pnl = total_equity - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

        # ── Cash balance — prefer manual override, fallback to Plaid ────────
        cash = 0.0

        # 1. Check users.cash_override
        try:
            user_row = (
                self.client.table("users")
                .select("cash_override")
                .eq("id", str(self.user_id))
                .single()
                .execute()
            ).data
            if user_row and user_row.get("cash_override") is not None:
                cash = float(user_row["cash_override"])
            else:
                raise ValueError("no override")
        except Exception:
            # 2. Fall back to last Plaid sync
            try:
                last_sync = (
                    self.client.table("plaid_sync_log")
                    .select("cash_balance")
                    .eq("user_id", str(self.user_id))
                    .eq("status", "success")
                    .order("synced_at", desc=True)
                    .limit(1)
                    .execute()
                ).data
                if last_sync:
                    cash = float(last_sync[0].get("cash_balance", 0))
            except Exception:
                pass

        # ── Day change — compute from price_history previous close ───────────
        day_change = 0.0
        day_change_pct = 0.0
        try:
            # Fetch most recent price_history entry per ticker for this user
            prev_closes: dict[str, float] = {}
            for ticker in tickers:
                ph_rows = (
                    self.client.table("price_history")
                    .select("close_price, price_date")
                    .eq("ticker", ticker)
                    .order("price_date", desc=True)
                    .limit(1)
                    .execute()
                ).data
                if ph_rows:
                    prev_closes[ticker] = float(ph_rows[0].get("close_price") or 0)

            # day_change = sum(shares * (current_price - prev_close)) for all positions
            if prev_closes:
                for p in positions:
                    ticker = p["ticker"]
                    current_price = prices.get(ticker)
                    prev_close = prev_closes.get(ticker)
                    if current_price and prev_close and prev_close > 0:
                        shares = float(p["shares"])
                        day_change += shares * (current_price - prev_close)

                equity_before_change = total_equity - day_change
                if equity_before_change > 0:
                    day_change_pct = day_change / equity_before_change * 100
        except Exception:
            pass

        return PortfolioSummary(
            total_equity=round(total_equity + cash, 2),
            total_cost=round(total_cost, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 4),
            cash_balance=round(cash, 2),
            day_change=round(day_change, 2),
            day_change_pct=round(day_change_pct, 4),
            stocks_value=round(stocks_value, 2),
            etfs_value=round(etfs_value, 2),
            crypto_value=round(crypto_value, 2),
            positions_count=len(positions),
            prices_fresh=fresh_count,
            prices_stale=stale_count,
            last_price_fetch=datetime.now(timezone.utc) if fresh_count > 0 else None,
        )

    async def list_snapshots(self, limit: int = 50) -> list[SnapshotResponse]:
        """List portfolio snapshots, newest first."""
        result = (
            self.client.table("portfolio_snapshots")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("snapshot_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data

    async def create_snapshot(self) -> SnapshotResponse:
        """Create a point-in-time snapshot using current data."""
        summary = await self.get_summary()

        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data

        snapshot = {
            "user_id": str(self.user_id),
            "total_equity": summary.total_equity,
            "total_cost": summary.total_cost,
            "total_pnl": summary.total_pnl,
            "total_pnl_pct": summary.total_pnl_pct,
            "cash_balance": summary.cash_balance,
            "positions_data": positions,
            "metadata": {
                "prices_fresh": summary.prices_fresh,
                "prices_stale": summary.prices_stale,
                "source": "manual_snapshot",
            },
        }

        result = self.client.table("portfolio_snapshots").insert(snapshot).execute()
        return result.data[0]

    async def list_targets(self) -> list[TargetAllocationResponse]:
        result = (
            self.client.table("target_allocations")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("ticker")
            .execute()
        )
        return result.data

    async def set_targets(self, targets: list[TargetAllocationCreate]) -> list[TargetAllocationResponse]:
        results = []
        for t in targets:
            data = {
                "user_id": str(self.user_id),
                "ticker": t.ticker.upper(),
                "target_pct": float(t.target_pct),
            }
            result = (
                self.client.table("target_allocations")
                .upsert(data, on_conflict="user_id,ticker")
                .execute()
            )
            results.extend(result.data)
        return results

    async def calculate_rebalance(self, cash_to_deploy: Optional[float] = None) -> list[RebalanceResult]:
        """Calculate rebalance suggestions based on targets vs current allocation.

        Falls back to the built-in deposit formula (NVDA 28% / VOO 22% / VYM 17% /
        QQQ 17% / ROTATING 16%) when no user targets are set.  Results are enriched
        with Intel recommendation signals and DRIP yield data.
        """
        targets_raw = await self.list_targets()
        using_default_formula = not targets_raw

        if using_default_formula:
            formula_targets: list[dict] = [
                {"ticker": tkr, "target_pct": pct} for tkr, pct in _DEPOSIT_FORMULA
            ]
        else:
            formula_targets = [
                {
                    "ticker": (t["ticker"] if isinstance(t, dict) else t.ticker),
                    "target_pct": float(t["target_pct"] if isinstance(t, dict) else t.target_pct),
                }
                for t in targets_raw
            ]

        summary = await self.get_summary()
        total = summary.total_equity + (cash_to_deploy or 0)
        if total <= 0:
            return []

        positions = (
            self.client.table("positions")
            .select("ticker, shares, avg_cost")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        position_map: dict[str, float] = {
            p["ticker"]: float(p["shares"]) * float(p["avg_cost"])
            for p in positions
        }

        # ── Fetch active Intel recommendations ───────────────────────────────
        intel_map: dict[str, dict] = {}
        try:
            recs = (
                self.client.table("recommendations")
                .select("ticker, action, urgency, detail, drip_note")
                .eq("user_id", str(self.user_id))
                .eq("is_active", True)
                .execute()
            ).data or []
            for r in recs:
                intel_map[r["ticker"].upper()] = {
                    "action":    r.get("action", "HOLD"),
                    "urgency":   int(r.get("urgency") or 0),
                    "detail":    r.get("detail") or "",
                    "drip_note": r.get("drip_note") or "",
                }
        except Exception as exc:
            logger.debug("Could not fetch Intel recs for rebalance: %s", exc)

        # ── Resolve ROTATING slot to best Intel BUY pick ─────────────────────
        formula_ticker_set = {
            t["ticker"].upper() for t in formula_targets
            if t["ticker"].upper() != "ROTATING"
        }
        rotating_resolved: Optional[str] = None

        for i, t in enumerate(formula_targets):
            if t["ticker"].upper() == "ROTATING":
                best: Optional[str] = None
                best_urgency = -1
                for tkr, sig in intel_map.items():
                    if (
                        tkr not in formula_ticker_set
                        and sig["action"] == "BUY"
                        and sig["urgency"] > best_urgency
                    ):
                        best = tkr
                        best_urgency = sig["urgency"]
                if best:
                    rotating_resolved = best
                    formula_targets[i] = {"ticker": best, "target_pct": t["target_pct"]}
                break  # only one ROTATING slot

        # ── Build results ─────────────────────────────────────────────────────
        results: list[RebalanceResult] = []
        for t in formula_targets:
            ticker = t["ticker"]
            target_pct = float(t["target_pct"])
            current_value = position_map.get(ticker, 0.0)
            current_pct = (current_value / total * 100) if total else 0.0
            drift = current_pct - target_pct

            if using_default_formula and cash_to_deploy:
                # Deposit mode: split the new cash directly by formula percentage
                deploy_amount = cash_to_deploy * target_pct / 100
                action = f"BUY ${deploy_amount:.2f}"
                amount = deploy_amount
            else:
                target_value = total * target_pct / 100
                diff = target_value - current_value
                if abs(drift) < 0.5:
                    action = "ON TARGET"
                    amount = 0.0
                elif drift < 0:
                    action = f"BUY ${abs(diff):.2f}"
                    amount = abs(diff)
                else:
                    action = f"SELL ${abs(diff):.2f}"
                    amount = -abs(diff)

            # Intel enrichment
            intel = intel_map.get(ticker.upper(), {})
            intel_action: Optional[str] = intel.get("action")
            intel_urgency: Optional[int] = intel.get("urgency")

            # DRIP note — prefer live Intel drip_note, fall back to yield map
            drip_note: Optional[str] = intel.get("drip_note") or None
            if not drip_note:
                yield_pct = _DRIP_YIELD_MAP.get(ticker.upper())
                if yield_pct:
                    drip_note = f"{yield_pct:.1f}% annual dividend yield"

            # Rationale
            rationale: Optional[str] = None
            if using_default_formula:
                ticker_up = ticker.upper()
                if rotating_resolved and ticker_up == rotating_resolved.upper():
                    rationale = (
                        f"Intel rotating pick: {intel.get('detail') or 'Best active BUY signal'}"
                    )
                elif ticker_up == "ROTATING":
                    rationale = "Rotating slot — no active Intel BUY signal found. Check Intel tab."
                else:
                    rationale = _FORMULA_RATIONALE.get(ticker_up)

            results.append(RebalanceResult(
                ticker=ticker,
                current_pct=round(current_pct, 2),
                target_pct=target_pct,
                drift_pct=round(drift, 2),
                suggested_action=action,
                suggested_amount=round(amount, 2),
                intel_action=intel_action,
                intel_urgency=intel_urgency,
                drip_note=drip_note,
                rationale=rationale,
                is_default_formula=using_default_formula,
            ))

        if not using_default_formula:
            results.sort(key=lambda r: r.drift_pct)
        return results

    async def backfill_snapshots_from_transactions(self) -> dict:
        """Reconstruct historical portfolio snapshots from transaction history.

        Algorithm:
        1. Load all Buy/Sell transactions sorted by date.
        2. Walk forward day-by-day and maintain running share counts per ticker.
        3. For each week-end boundary (Saturday) within the transaction range,
           look up close prices from price_history and compute total_equity.
        4. Insert portfolio_snapshots for dates not already recorded.

        Returns {created, skipped, message}.
        """
        # ── 1. Load all buy/sell transactions ───────────────────────────────
        tx_rows = (
            self.client.table("transactions")
            .select("ticker, tx_type, quantity, price, tx_date")
            .eq("user_id", str(self.user_id))
            .in_("tx_type", ["Buy", "Sell"])
            .order("tx_date", desc=False)
            .execute()
        ).data

        if not tx_rows:
            return {"created": 0, "skipped": 0, "message": "No Buy/Sell transactions found"}

        # ── 2. Build daily position state ────────────────────────────────────
        # shares[ticker] = running share count
        shares: dict[str, float] = defaultdict(float)
        # cost[ticker] = running total cost (for avg_cost)
        total_cost_map: dict[str, float] = defaultdict(float)

        # date_str -> snapshot of shares at end of that day
        # We capture state after every transaction date
        daily_states: dict[str, dict[str, dict]] = {}  # date -> {ticker: {shares, avg_cost}}

        for tx in tx_rows:
            ticker = (tx.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            qty = float(tx.get("quantity") or 0)
            price = float(tx.get("price") or 0)
            tx_type = tx.get("tx_type", "")
            tx_date = tx.get("tx_date", "")[:10]

            if tx_type == "Buy":
                shares[ticker] += qty
                total_cost_map[ticker] += qty * price
            elif tx_type == "Sell":
                current = shares[ticker]
                if current > 0:
                    # Reduce cost proportionally
                    sell_fraction = min(qty / current, 1.0)
                    total_cost_map[ticker] -= total_cost_map[ticker] * sell_fraction
                shares[ticker] = max(0.0, current - qty)
                if shares[ticker] < 0.0001:
                    shares[ticker] = 0.0
                    total_cost_map[ticker] = 0.0

            # Save state snapshot for this date
            daily_states[tx_date] = {
                t: {
                    "shares": s,
                    "avg_cost": (total_cost_map[t] / s) if s > 0 else 0.0,
                }
                for t, s in shares.items()
                if s > 0.0001
            }

        if not daily_states:
            return {"created": 0, "skipped": 0, "message": "No valid position states computed"}

        first_date = date.fromisoformat(min(daily_states.keys()))
        last_date = date.today()

        # ── 3. Determine target snapshot dates (weekly on Saturdays) ─────────
        snapshot_dates: list[date] = []
        cursor = first_date
        # Advance to nearest Saturday
        days_to_sat = (5 - cursor.weekday()) % 7
        cursor += timedelta(days=days_to_sat)
        while cursor <= last_date:
            snapshot_dates.append(cursor)
            cursor += timedelta(weeks=1)

        # ── 4. Get existing snapshot dates to avoid duplicates ───────────────
        existing = (
            self.client.table("portfolio_snapshots")
            .select("snapshot_at")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data
        existing_date_strs = {r["snapshot_at"][:10] for r in existing}

        # ── 5. Fetch all price_history for all tickers in one query ──────────
        all_tickers = list({t for state in daily_states.values() for t in state})
        # Prices: {ticker: {date_str: close_price}}
        price_lookup: dict[str, dict[str, float]] = defaultdict(dict)

        if all_tickers:
            start_str = first_date.isoformat()
            ph_rows = (
                self.client.table("price_history")
                .select("ticker, price_date, close_price")
                .in_("ticker", all_tickers)
                .gte("price_date", start_str)
                .execute()
            ).data
            for row in ph_rows:
                t = row["ticker"]
                d_str = str(row["price_date"])[:10]
                cp = float(row.get("close_price") or 0)
                if cp > 0:
                    price_lookup[t][d_str] = cp

        def _get_price(ticker: str, date_str: str) -> Optional[float]:
            """Get close price for ticker on or before date_str."""
            if ticker not in price_lookup:
                return None
            # Look for closest date on or before date_str
            prices = price_lookup[ticker]
            candidates = [d for d in prices if d <= date_str]
            if not candidates:
                return None
            return prices[max(candidates)]

        def _get_position_state(target_date_str: str) -> dict[str, dict]:
            """Get most recent position state on or before target_date_str."""
            candidates = [d for d in daily_states if d <= target_date_str]
            if not candidates:
                return {}
            return daily_states[max(candidates)]

        # ── 6. Create snapshots ───────────────────────────────────────────────
        created = 0
        skipped = 0
        to_insert = []

        for snap_date in snapshot_dates:
            date_str = snap_date.isoformat()
            if date_str in existing_date_strs:
                skipped += 1
                continue

            position_state = _get_position_state(date_str)
            if not position_state:
                skipped += 1
                continue

            total_equity = 0.0
            total_cost = 0.0
            priced_count = 0

            for ticker, state in position_state.items():
                s = state["shares"]
                ac = state["avg_cost"]
                total_cost += s * ac

                p = _get_price(ticker, date_str)
                if p:
                    total_equity += s * p
                    priced_count += 1
                else:
                    # Fallback to cost basis for unpriced tickers
                    total_equity += s * ac

            if total_equity <= 0 or priced_count == 0:
                skipped += 1
                continue

            total_pnl = total_equity - total_cost
            total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

            to_insert.append({
                "user_id": str(self.user_id),
                "snapshot_at": f"{date_str}T20:00:00+00:00",  # ~4pm ET close
                "total_equity": round(total_equity, 2),
                "total_cost": round(total_cost, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl_pct, 4),
                "cash_balance": 0.0,
                "positions_data": [
                    {"ticker": t, "shares": s["shares"], "avg_cost": s["avg_cost"]}
                    for t, s in position_state.items()
                ],
                "metadata": {"source": "backfill_from_transactions"},
            })
            existing_date_strs.add(date_str)

        # Batch insert
        for i in range(0, len(to_insert), 50):
            batch = to_insert[i:i + 50]
            try:
                self.client.table("portfolio_snapshots").insert(batch).execute()
                created += len(batch)
            except Exception as exc:
                logger.error("Backfill batch insert failed: %s", exc)
                skipped += len(batch)

        return {
            "created": created,
            "skipped": skipped,
            "message": f"Backfilled {created} weekly snapshots from transaction history",
        }


# ── Portfolio engine adapter ──────────────────────────────────────────────────

async def get_portfolio_snapshot(user_id: UUID) -> dict[str, Any]:
    """Fetch transactions and positions from Supabase, then compute a snapshot.

    Orchestrates the pure functions in portfolio_engine:
      1. Load raw transactions from Supabase.
      2. Normalise and aggregate into positions via portfolio_engine.
      3. Fetch live prices via the price engine.
      4. Return the full snapshot dict produced by build_portfolio_snapshot().
    """
    from .portfolio_engine import normalize_transactions, build_positions, build_portfolio_snapshot
    from .price_engine import PriceService
    from ..config import get_settings
    from ..database import get_supabase_client

    client = get_supabase_client()
    settings = get_settings()

    # 1. Fetch raw transactions (Buy/Sell only)
    tx_rows: list[dict[str, Any]] = (
        client.table("transactions")
        .select("ticker, tx_type, quantity, price, tx_date, category")
        .eq("user_id", str(user_id))
        .in_("tx_type", ["Buy", "Sell"])
        .order("tx_date", desc=False)
        .execute()
    ).data or []

    # 2. Normalise and build positions using pure engine functions
    normalised = normalize_transactions(tx_rows)
    positions = build_positions(normalised)

    if not positions:
        return {
            "total_value": 0.0,
            "total_cost": 0.0,
            "total_pnl": 0.0,
            "total_pnl_percent": 0.0,
            "positions": [],
        }

    # 3. Fetch live prices for all symbols
    symbols = [p["symbol"] for p in positions]
    price_service = PriceService(
        finnhub_key=settings.finnhub_api_key or "",
        alpaca_key=settings.alpaca_api_key or "",
        alpaca_secret=settings.alpaca_secret_key or "",
        polygon_key=settings.polygon_api_key or "",
    )

    prices: dict[str, float] = {}
    try:
        price_results = await price_service.fetch_prices(symbols)
        for sym, pr in price_results.items():
            if pr.is_valid:
                prices[sym] = pr.mid_price
    except Exception:
        logger.warning("Price fetch failed for portfolio snapshot; using cost basis fallback")

    # 4. Compute and return snapshot
    return build_portfolio_snapshot(positions, prices)
