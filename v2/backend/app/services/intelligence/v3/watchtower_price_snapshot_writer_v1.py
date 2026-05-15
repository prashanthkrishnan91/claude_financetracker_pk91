"""Watchtower price snapshot writer v1 (Build 1D).

Persists refreshed price results to portfolio_snapshots so the evidence
collector can read them back as fresh evidence. This is the durable write
path that makes Watchtower price refresh observable to future gate checks.

Flow:
  1. Read current positions (ticker, shares, avg_cost) for the user.
  2. For tickers where price refresh succeeded: compute market_value,
     set market_value_certified_at = now.
  3. For tickers where price refresh failed: carry forward the last-known
     market_value and DO NOT set market_value_certified_at to now.
     (Fake freshness is forbidden: never stamp old evidence as current.)
  4. Compute total_equity / total_cost / total_pnl for NOT NULL constraints.
  5. Insert a new portfolio_snapshots row with snapshot_at = now.

Pure DB write — no LLM calls, no provider calls.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class PersistResult:
    """Result of one watchtower price snapshot persist call."""
    snapshot_id: Optional[str] = None
    persisted: bool = False
    certified_ticker_count: int = 0   # tickers with fresh market_value_certified_at
    carried_ticker_count: int = 0     # tickers where price failed; old value carried
    total_positions: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id":           self.snapshot_id,
            "persisted":             self.persisted,
            "certified_ticker_count": self.certified_ticker_count,
            "carried_ticker_count":   self.carried_ticker_count,
            "total_positions":        self.total_positions,
            "error":                  self.error,
        }


async def persist_watchtower_price_snapshot(
    user_id: UUID,
    client: Any,
    *,
    price_results: dict[str, Any],
    now: Optional[datetime] = None,
) -> PersistResult:
    """Write a new portfolio_snapshots row from refreshed price results.

    Args:
        user_id: The user whose snapshot to write.
        client: Supabase client (service-role).
        price_results: Mapping of ticker → PriceResult (or None on failure).
            Any ticker with a non-None result that passes is_valid/is_stale checks
            gets market_value_certified_at = now.
        now: Override clock (default: UTC now).

    Returns:
        PersistResult describing what was written.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    result = PersistResult()

    try:
        # Step 1: read current positions
        positions = await asyncio.to_thread(
            lambda: client.table("positions")
            .select("ticker,shares,avg_cost")
            .eq("user_id", str(user_id))
            .execute()
        )
        pos_rows = positions.data or []
        result.total_positions = len(pos_rows)

        if not pos_rows:
            logger.info(
                "watchtower_price_snapshot_writer.no_positions user_id=%s", user_id,
            )
            result.error = "no_positions"
            return result

        # Step 2: read latest snapshot for carry-forward values
        snap_result = await asyncio.to_thread(
            lambda: client.table("portfolio_snapshots")
            .select("positions_data")
            .eq("user_id", str(user_id))
            .order("snapshot_at", desc=True)
            .limit(1)
            .execute()
        )
        prev_rows = snap_result.data or []
        prev_positions_data: list[dict] = []
        if prev_rows and isinstance(prev_rows[0], dict):
            prev_positions_data = prev_rows[0].get("positions_data") or []

        # Build lookup: ticker → previous position entry for carry-forward
        prev_by_ticker: dict[str, dict] = {}
        for entry in prev_positions_data:
            if isinstance(entry, dict):
                t = (entry.get("ticker") or "").upper()
                if t:
                    prev_by_ticker[t] = entry

        # Step 3 & 4: build enriched positions_data and compute totals
        certified_at_str = now.isoformat()
        enriched: list[dict] = []
        total_equity = 0.0
        total_cost = 0.0

        for pos in pos_rows:
            ticker = (pos.get("ticker") or "").upper()
            if not ticker:
                continue
            shares = float(pos.get("shares") or 0)
            avg_cost = float(pos.get("avg_cost") or 0)
            cost_basis = shares * avg_cost

            # Try refreshed price (also check dot-to-dash normalization)
            price_res = price_results.get(ticker) or price_results.get(
                ticker.replace(".", "-")
            )

            entry: dict[str, Any] = {
                "ticker": ticker,
                "shares": shares,
                "avg_cost": avg_cost,
            }

            if (
                price_res is not None
                and getattr(price_res, "is_valid", lambda: False)()
                and not getattr(price_res, "is_stale", lambda: False)()
            ):
                # Fresh price: compute and certify
                mid = float(getattr(price_res, "mid_price", 0) or 0)
                if mid > 0:
                    market_value = shares * mid
                    entry["market_price_usd"] = round(mid, 6)
                    entry["market_value_usd"] = round(market_value, 2)
                    entry["market_value_source"] = getattr(price_res, "source", "watchtower")
                    entry["market_value_certified_at"] = certified_at_str
                    total_equity += market_value
                    result.certified_ticker_count += 1
                else:
                    total_equity += cost_basis
                    _carry_forward_market_value(entry, prev_by_ticker.get(ticker), cost_basis)
                    result.carried_ticker_count += 1
            else:
                # Price unavailable or stale — carry forward, never stamp as certified
                total_equity += _carry_forward_market_value(
                    entry, prev_by_ticker.get(ticker), cost_basis
                )
                result.carried_ticker_count += 1

            total_cost += cost_basis
            enriched.append(entry)

        total_pnl = total_equity - total_cost

        # Step 5: insert new snapshot row
        snapshot_payload = {
            "user_id": str(user_id),
            "total_equity": round(total_equity, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2),
            "positions_data": enriched,
            "metadata": {
                "source": "watchtower_price_refresh",
                "certified_ticker_count": result.certified_ticker_count,
                "carried_ticker_count": result.carried_ticker_count,
            },
        }

        insert_result = await asyncio.to_thread(
            lambda: client.table("portfolio_snapshots")
            .insert(snapshot_payload)
            .execute()
        )
        inserted_rows = insert_result.data or []
        if inserted_rows:
            result.snapshot_id = str(
                (inserted_rows[0] or {}).get("id") or
                (inserted_rows[0] or {}).get("snapshot_id") or
                "unknown"
            )
        result.persisted = True

        logger.info(
            "watchtower_price_snapshot_writer.snapshot_written user_id=%s "
            "certified=%d carried=%d total=%d",
            user_id, result.certified_ticker_count,
            result.carried_ticker_count, result.total_positions,
        )

    except Exception as exc:
        result.error = str(exc)
        logger.warning(
            "watchtower_price_snapshot_writer.failed user_id=%s error=%s",
            user_id, exc,
        )

    return result


def _carry_forward_market_value(
    entry: dict,
    prev_entry: Optional[dict],
    cost_basis: float,
) -> float:
    """Copy last-known market_value from previous snapshot into entry.

    Does NOT copy market_value_certified_at — old cert times must not be
    propagated into the new row (that would fake freshness for deploy gate).
    Returns the market_value used for total_equity computation.
    """
    if prev_entry and prev_entry.get("market_value_usd") is not None:
        try:
            mv = float(prev_entry["market_value_usd"])
            entry["market_value_usd"] = round(mv, 2)
            if prev_entry.get("market_price_usd") is not None:
                entry["market_price_usd"] = prev_entry["market_price_usd"]
            if prev_entry.get("market_value_source") is not None:
                entry["market_value_source"] = prev_entry["market_value_source"]
            # market_value_certified_at intentionally NOT carried forward
            return mv
        except (TypeError, ValueError):
            pass
    return cost_basis
