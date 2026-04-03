"""
main_sync.py — Portfolio War Room v11.1
Smart Sync CLI — displays live Total Equity in the terminal.

Plaid API call frequency:
  - First run:  always calls Plaid (no cache)
  - Subsequent: calls Plaid only if holdings_cache.json > 24h old
  - Prices:     refreshed every --interval seconds (default 60) — NO Plaid calls

Usage:
    python main_sync.py                     # sync once, print, exit
    python main_sync.py --loop              # refresh prices every 60s
    python main_sync.py --loop 30           # refresh every 30s
    python main_sync.py --async             # async mode (aiohttp)
    python main_sync.py --force-plaid       # force Plaid re-sync now
    python main_sync.py --cache-status      # show holdings cache status, exit
    python main_sync.py --loop --no-color   # CI-friendly output

Environment variables (.env or shell):
    PLAID_CLIENT_ID        required
    PLAID_SECRET           required
    PLAID_ENV              sandbox | development | production
    PLAID_ACCESS_TOKEN     required
    FINNHUB_API_KEY        required
    POLYGON_API_KEY        optional fallback
    HOLDINGS_CACHE_PATH    override cache file path (default: holdings_cache.json)
    HOLDINGS_CACHE_TTL_HOURS  override 24h TTL (default: 24)
    PORTFOLIO_LOG_LEVEL    DEBUG | INFO | WARNING (default: INFO)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[env] Loaded .env from {env_path}", file=sys.stderr)
except ImportError:
    pass

from holdings_manager import HoldingsManager
from portfolio_aggregator import PortfolioAggregator, PortfolioSnapshot

# ─── Logging ──────────────────────────────────────────────────────────────────
_LOG_LEVEL = os.environ.get("PORTFOLIO_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("main_sync")


# ═══════════════════════════════════════════════════════════════════════════════
# ANSI COLORS
# ═══════════════════════════════════════════════════════════════════════════════

_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"


def _c(text: str, code: str, use_color: bool) -> str:
    return f"{code}{text}{_RESET}" if use_color else text


def _pnl_color(val: float, use_color: bool) -> str:
    if not use_color:
        return f"{val:+.2f}"
    return f"{_GREEN}{val:+.2f}{_RESET}" if val >= 0 else f"{_RED}{val:+.2f}{_RESET}"


# ═══════════════════════════════════════════════════════════════════════════════
# PRETTY PRINTER
# ═══════════════════════════════════════════════════════════════════════════════

def print_snapshot(snapshot: PortfolioSnapshot, color: bool = True) -> None:
    ts  = datetime.fromtimestamp(snapshot.snapshot_timestamp, tz=timezone.utc)
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC")

    WIDE = "═" * 68
    SEP  = "─" * 68

    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{_c(WIDE, _BOLD, color)}")
    title = f"  ⚡  PORTFOLIO WAR ROOM  —  Smart Sync  ({ts_str})"
    print(_c(title, _BOLD, color))

    # Smart sync status badge
    if snapshot.plaid_sync_triggered:
        sync_badge = _c("  🏦 PLAID SYNCED (holdings refreshed)", _GREEN, color)
    else:
        age = snapshot.holdings_cache_age_h
        sync_badge = _c(f"  📦 CACHE HIT (holdings {age:.1f}h old — no Plaid call)", _CYAN, color)
    print(sync_badge)
    print(_c(WIDE, _BOLD, color))

    # ── Totals ────────────────────────────────────────────────────────────────
    pnl_str = _pnl_color(snapshot.total_unrealised_pnl, color)
    pct_str = _pnl_color(snapshot.total_unrealised_pct, color) + "%"

    print(f"\n  {_c('TOTAL EQUITY', _BOLD, color):<35}  "
          f"{_c(f'${snapshot.total_equity:>12,.2f}', _BOLD, color)}")
    print(f"  {'  Stocks & ETFs':<35}  ${snapshot.stocks_equity:>12,.2f}")
    print(f"  {'  Crypto':<35}  ${snapshot.crypto_equity:>12,.2f}")
    print(f"  {'  Cash':<35}  ${snapshot.cash_usd:>12,.2f}")
    print(f"  {'Total Cost Basis':<35}  ${snapshot.total_cost_basis:>12,.2f}")
    print(f"  {'Unrealised P&L':<35}  {pnl_str:>14}  ({pct_str})")

    if snapshot.stale_prices:
        print(f"\n  {_c('⚠️  Stale prices:', _YELLOW, color)} {', '.join(snapshot.stale_prices)}")
    if snapshot.failed_prices:
        print(f"  {_c('❌  Failed prices:', _RED, color)} {', '.join(snapshot.failed_prices)}")

    # ── Positions table ───────────────────────────────────────────────────────
    print(f"\n{SEP}")
    hdr = (f"  {'TICKER':<8}  {'QTY':>10}  {'MID PRICE':>11}  "
           f"{'VALUE':>11}  {'P&L%':>8}  {'BID':>8}  {'ASK':>8}  SRC")
    print(_c(hdr, _DIM, color))
    print(SEP)

    for pos in sorted(snapshot.positions, key=lambda p: -p.market_value):
        qty_s   = f"{pos.quantity:.6f}".rstrip("0").rstrip(".")
        pr_s    = f"${pos.mid_price:,.4f}" if pos.mid_price < 10 else f"${pos.mid_price:,.2f}"
        val_s   = f"${pos.market_value:,.2f}"
        bid_s   = f"${pos.bid:,.4f}" if pos.bid else "  —"
        ask_s   = f"${pos.ask:,.4f}" if pos.ask else "  —"
        pnl_raw = pos.unrealised_pct
        pnl_s   = _pnl_color(pnl_raw, color) + "%"
        stale   = _c(" ⚠", _YELLOW, color) if pos.price_stale else ""
        src     = (pos.price_source[:10] + stale) if pos.price_source else "?"

        print(f"  {pos.ticker:<8}  {qty_s:>10}  {pr_s:>11}  {val_s:>11}  {pnl_s:>8}  "
              f"{bid_s:>8}  {ask_s:>8}  {src}")

    print(f"\n{WIDE}")
    print(f"  {snapshot.positions_count} positions  |  "
          f"Accounts: {', '.join(snapshot.plaid_account_ids) or 'N/A'}  |  "
          f"Holdings cache: {snapshot.holdings_cache_age_h:.1f}h old")
    print(WIDE + "\n")


def print_cache_status(manager: HoldingsManager, color: bool = True) -> None:
    """Print current holdings cache status without making any API calls."""
    status = manager.get_cache_status()
    print(f"\n{'─'*50}")
    print("  🗄️  Holdings Cache Status")
    print(f"{'─'*50}")
    label_color = _GREEN if status["status"] == "fresh" else _YELLOW
    print(f"  Status:        {_c(status['label'], label_color, color)}")
    print(f"  Last synced:   {status['last_synced'] or 'Never'}")
    print(f"  Age:           {f\"{status['age_hours']:.2f}h\" if status['age_hours'] is not None else 'N/A'}")
    print(f"  Holdings:      {status['holdings_count']} positions")
    print(f"  Cash (Plaid):  ${status['cash_usd']:.2f}")
    if status["next_sync_in"] is not None and status["next_sync_in"] > 0:
        print(f"  Next Plaid:    in {status['next_sync_in']:.2f}h")
    else:
        print(f"  Next Plaid:    {_c('DUE NOW', _YELLOW, color)}")
    print(f"{'─'*50}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# JSON EXPORT  (consumed by Streamlit data_engine.py)
# ═══════════════════════════════════════════════════════════════════════════════

def export_json(snapshot: PortfolioSnapshot, path: str = "portfolio_snapshot.json") -> None:
    data = {
        "timestamp":              snapshot.snapshot_timestamp,
        "total_equity":           round(snapshot.total_equity,         2),
        "stocks_equity":          round(snapshot.stocks_equity,        2),
        "crypto_equity":          round(snapshot.crypto_equity,        2),
        "cash_usd":               round(snapshot.cash_usd,             2),
        "total_cost_basis":       round(snapshot.total_cost_basis,     2),
        "total_unrealised_pnl":   round(snapshot.total_unrealised_pnl, 2),
        "total_unrealised_pct":   round(snapshot.total_unrealised_pct, 4),
        "stale_prices":           snapshot.stale_prices,
        "failed_prices":          snapshot.failed_prices,
        "plaid_account_ids":      snapshot.plaid_account_ids,
        "holdings_cache_age_h":   snapshot.holdings_cache_age_h,
        "plaid_sync_triggered":   snapshot.plaid_sync_triggered,
        "positions": [
            {
                "ticker":         pos.ticker,
                "name":           pos.name,
                "quantity":       pos.quantity,
                "avg_cost_basis": round(pos.avg_cost_basis, 6),
                "mid_price":      round(pos.mid_price,      6),
                "market_value":   round(pos.market_value,   2),
                "cost_total":     round(pos.cost_total,     2),
                "unrealised_pnl": round(pos.unrealised_pnl, 2),
                "unrealised_pct": round(pos.unrealised_pct, 4),
                "bid":            round(pos.bid, 6) if pos.bid else None,
                "ask":            round(pos.ask, 6) if pos.ask else None,
                "last_trade":     round(pos.last_trade, 6),
                "price_source":   pos.price_source,
                "security_type":  pos.security_type,
                "price_stale":    pos.price_stale,
                "price_error":    pos.price_error,
            }
            for pos in snapshot.positions
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Snapshot exported → %s", path)


# ═══════════════════════════════════════════════════════════════════════════════
# SYNC RUNNERS
# ═══════════════════════════════════════════════════════════════════════════════

def run_once(
    force_plaid: bool = False,
    color: bool = True,
    export: bool = True,
) -> PortfolioSnapshot:
    """Run one sync cycle: load holdings (cache or Plaid) + fetch live prices."""
    agg      = PortfolioAggregator()
    snapshot = agg.calculate_total_value(force_plaid_refresh=force_plaid)
    print_snapshot(snapshot, color=color)
    if export:
        export_json(snapshot)
    return snapshot


async def run_once_async(
    force_plaid: bool = False,
    color: bool = True,
    export: bool = True,
) -> PortfolioSnapshot:
    """Async version — fully async price fetch via aiohttp."""
    agg      = PortfolioAggregator()
    snapshot = await agg.calculate_total_value_async(force_plaid_refresh=force_plaid)
    print_snapshot(snapshot, color=color)
    if export:
        export_json(snapshot)
    return snapshot


def run_price_loop(
    interval_seconds: int = 60,
    color: bool = True,
    use_async: bool = False,
) -> None:
    """
    Smart Sync continuous loop.

    Holdings: refreshed from Plaid only when cache >24h old (or on first run).
    Prices:   refreshed every `interval_seconds` — ZERO Plaid calls per cycle.

    This is the core design of the Smart Sync architecture:
      - Plaid API calls: ~1 per day
      - Price API calls: every 60s (or as fast as you want)
    """
    agg = PortfolioAggregator()
    print(f"[smart-sync] Prices every {interval_seconds}s | "
          f"Plaid max once/24h | Ctrl+C to stop\n")

    cycle = 0
    while True:
        try:
            cycle += 1
            if use_async:
                snapshot = asyncio.run(agg.calculate_total_value_async())
            else:
                snapshot = agg.calculate_total_value()

            print_snapshot(snapshot, color=color)
            export_json(snapshot)

            print(f"  {_c(f'[cycle {cycle}] Next refresh in {interval_seconds}s…', _DIM, color)}\n")

        except KeyboardInterrupt:
            print(f"\n[smart-sync] Stopped after {cycle} cycles.")
            break
        except Exception as exc:
            logger.error("Sync error (cycle %d): %s — retrying in %ds", cycle, exc, interval_seconds)

        time.sleep(interval_seconds)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Portfolio War Room v11.1 — Smart Sync CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python main_sync.py                    # sync once
  python main_sync.py --loop             # refresh prices every 60s
  python main_sync.py --loop 30          # refresh every 30s
  python main_sync.py --force-plaid      # force Plaid re-sync now
  python main_sync.py --cache-status     # show cache status, exit
  python main_sync.py --async --loop 60  # async price loop
        """,
    )
    parser.add_argument(
        "--loop", nargs="?", const=60, type=int, default=None,
        metavar="SECONDS",
        help="Run price-refresh loop. Interval in seconds (default: 60).",
    )
    parser.add_argument(
        "--async", dest="use_async", action="store_true",
        help="Use asyncio + aiohttp for price fetches.",
    )
    parser.add_argument(
        "--force-plaid", dest="force_plaid", action="store_true",
        help="Force a fresh Plaid holdings sync regardless of cache age.",
    )
    parser.add_argument(
        "--cache-status", dest="cache_status", action="store_true",
        help="Print holdings cache status and exit (no API calls).",
    )
    parser.add_argument(
        "--no-color", dest="no_color", action="store_true",
        help="Disable ANSI color codes.",
    )
    parser.add_argument(
        "--no-export", dest="no_export", action="store_true",
        help="Skip JSON export to portfolio_snapshot.json.",
    )
    args = parser.parse_args()

    color  = not args.no_color
    export = not args.no_export

    # Cache status check — no API calls
    if args.cache_status:
        mgr = HoldingsManager()
        print_cache_status(mgr, color=color)
        return

    # Continuous loop
    if args.loop is not None:
        run_price_loop(
            interval_seconds=args.loop,
            color=color,
            use_async=args.use_async,
        )
        return

    # One-shot
    if args.use_async:
        asyncio.run(run_once_async(
            force_plaid=args.force_plaid,
            color=color,
            export=export,
        ))
    else:
        run_once(
            force_plaid=args.force_plaid,
            color=color,
            export=export,
        )


if __name__ == "__main__":
    main()
