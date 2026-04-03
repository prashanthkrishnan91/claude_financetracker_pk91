"""
holdings_manager.py — Portfolio War Room v11.1
Smart Sync layer between Plaid API and the pricing engine.

Design goals:
  - Call Plaid's investments/holdings/get ONLY when necessary
  - Cache holdings to holdings_cache.json with a 24-hour TTL
  - Prices update independently every 60s without touching Plaid
  - Survive Plaid outages by serving stale cache with a warning
  - Never crash on ticker mismatches (BRK.B ↔ BRK-B handled here)

Cache schema (holdings_cache.json):
  {
    "last_synced":  "2026-04-03T14:22:00.123456",   # ISO-8601
    "account_ids":  ["acc_abc123"],
    "cash_usd":     1042.17,
    "holdings": [
      {
        "ticker":             "NVDA",
        "quantity":           35.504150,
        "cost_basis":         82.50,
        "institution_price":  875.22,   # Plaid's last known — used as fallback
        "security_type":      "equity",
        "name":               "NVIDIA Corp",
        "account_id":         "acc_abc123"
      },
      ...
    ]
  }
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Default cache path (override via HOLDINGS_CACHE_PATH env var) ─────────────
_DEFAULT_CACHE_PATH = Path(os.environ.get("HOLDINGS_CACHE_PATH", "holdings_cache.json"))

# ── How long before the cache is considered stale ────────────────────────────
_CACHE_TTL_HOURS = float(os.environ.get("HOLDINGS_CACHE_TTL_HOURS", "24"))


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CachedHolding:
    """
    Single holding entry stored in holdings_cache.json.
    Quantities from Plaid are authoritative.
    institution_price is Plaid's last-known price — used ONLY when all live
    price providers fail.
    """
    ticker:             str
    quantity:           float
    cost_basis:         float    # Average cost per share
    institution_price:  float    # Plaid's last-known price (fallback only)
    security_type:      str      # equity | etf | mutual fund | crypto | cash
    name:               str
    account_id:         str


@dataclass
class HoldingsCache:
    """Full cache payload written to / read from holdings_cache.json."""
    last_synced:  str                    # ISO-8601 UTC timestamp of last Plaid call
    account_ids:  list[str]
    cash_usd:     float
    holdings:     list[CachedHolding] = field(default_factory=list)

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def last_synced_dt(self) -> datetime:
        """Parse last_synced to a timezone-aware datetime."""
        try:
            dt = datetime.fromisoformat(self.last_synced)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=timezone.utc)

    @property
    def age_hours(self) -> float:
        """Hours elapsed since last Plaid sync."""
        delta = datetime.now(tz=timezone.utc) - self.last_synced_dt
        return delta.total_seconds() / 3600

    @property
    def is_stale(self) -> bool:
        """True if cache is older than _CACHE_TTL_HOURS."""
        return self.age_hours > _CACHE_TTL_HOURS

    @property
    def tickers(self) -> list[str]:
        """All normalised ticker symbols in cache."""
        return [h.ticker for h in self.holdings]

    def to_dict(self) -> dict:
        return {
            "last_synced": self.last_synced,
            "account_ids": self.account_ids,
            "cash_usd":    self.cash_usd,
            "holdings":    [asdict(h) for h in self.holdings],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HoldingsCache":
        holdings = [
            CachedHolding(**{k: v for k, v in h.items() if k in CachedHolding.__dataclass_fields__})
            for h in data.get("holdings", [])
        ]
        return cls(
            last_synced  = data.get("last_synced", ""),
            account_ids  = data.get("account_ids", []),
            cash_usd     = float(data.get("cash_usd", 0)),
            holdings     = holdings,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# HOLDINGS MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class HoldingsManager:
    """
    Smart cache layer between Plaid and the pricing engine.

    Sync Strategy:
        Call Plaid ONLY when one of these is true:
          1. holdings_cache.json does not exist
          2. Cache is older than 24 hours (configurable via HOLDINGS_CACHE_TTL_HOURS)
          3. force_refresh=True is passed explicitly (e.g. sidebar "Sync Plaid" button)

        Between Plaid syncs, prices are refreshed by PriceService (Finnhub/Polygon)
        every 60 seconds without touching Plaid at all.

    Usage:
        manager = HoldingsManager()
        cache   = manager.get_holdings()          # uses cache if fresh
        cache   = manager.get_holdings(force_refresh=True)  # forces Plaid call

        # Check what triggered a sync
        needs_sync, reason = manager.needs_plaid_sync()
    """

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        plaid_client=None,          # PlaidClient instance (injected or lazy-created)
    ) -> None:
        self._cache_path = cache_path or _DEFAULT_CACHE_PATH
        self._plaid      = plaid_client   # None → lazy-create on first real sync
        self._memory_cache: Optional[HoldingsCache] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_holdings(
        self,
        force_refresh: bool = False,
        account_ids: Optional[list[str]] = None,
    ) -> HoldingsCache:
        """
        Return current holdings — from local cache or Plaid, depending on state.

        Args:
            force_refresh:  If True, always call Plaid regardless of cache age.
            account_ids:    Optional Plaid account filter (only used on live fetch).

        Returns:
            HoldingsCache with authoritative quantities.

        Raises:
            RuntimeError: If Plaid sync is required but fails AND no cache exists.
        """
        needs_sync, reason = self.needs_plaid_sync(force_refresh=force_refresh)

        if needs_sync:
            logger.info("Plaid sync triggered: %s", reason)
            try:
                fresh = self._fetch_from_plaid(account_ids=account_ids)
                self._write_cache(fresh)
                self._memory_cache = fresh
                logger.info(
                    "Plaid sync complete: %d holdings, cash=$%.2f",
                    len(fresh.holdings), fresh.cash_usd
                )
                return fresh
            except Exception as exc:
                # Plaid failed — fall back to stale cache if it exists
                stale = self._read_cache()
                if stale:
                    logger.warning(
                        "Plaid sync failed (%s) — serving stale cache (%.1fh old)",
                        exc, stale.age_hours
                    )
                    return stale
                # No cache at all and Plaid failed → propagate
                logger.error("Plaid sync failed and no cache exists: %s", exc)
                raise RuntimeError(
                    f"Cannot load holdings: Plaid failed ({exc}) and no local cache found. "
                    f"Run once with a working Plaid connection to seed the cache."
                ) from exc

        # Cache is fresh — serve from memory or disk
        if self._memory_cache:
            logger.debug("Serving holdings from memory cache (%.1fh old)", self._memory_cache.age_hours)
            return self._memory_cache

        disk = self._read_cache()
        if disk:
            self._memory_cache = disk
            logger.debug("Serving holdings from disk cache (%.1fh old)", disk.age_hours)
            return disk

        # Shouldn't reach here — needs_plaid_sync should have caught this
        raise RuntimeError("No holdings cache available and Plaid sync was skipped.")

    def needs_plaid_sync(
        self,
        force_refresh: bool = False,
    ) -> tuple[bool, str]:
        """
        Evaluate whether a Plaid API call is necessary.

        Returns:
            (True, reason_string) if sync is needed
            (False, "cache is fresh") if cache can be served
        """
        if force_refresh:
            return True, "force_refresh=True"

        cache = self._memory_cache or self._read_cache()

        if cache is None:
            return True, "holdings_cache.json not found"

        if not cache.holdings:
            return True, "cache exists but contains no holdings"

        if cache.is_stale:
            return True, f"cache is {cache.age_hours:.1f}h old (TTL={_CACHE_TTL_HOURS}h)"

        return False, f"cache is fresh ({cache.age_hours:.2f}h old)"

    def get_cache_status(self) -> dict:
        """
        Return a human-readable status dict for UI display.
        Used by the Streamlit sidebar reconciliation panel.
        """
        cache = self._memory_cache or self._read_cache()
        if not cache:
            return {
                "status":         "no_cache",
                "label":          "Not synced",
                "age_hours":      None,
                "last_synced":    None,
                "holdings_count": 0,
                "cash_usd":       0.0,
                "is_stale":       True,
                "next_sync_in":   None,
            }

        next_sync_h = max(0.0, _CACHE_TTL_HOURS - cache.age_hours)
        return {
            "status":         "stale" if cache.is_stale else "fresh",
            "label":          "Stale — sync due" if cache.is_stale else "Fresh",
            "age_hours":      round(cache.age_hours, 2),
            "last_synced":    cache.last_synced,
            "holdings_count": len(cache.holdings),
            "cash_usd":       cache.cash_usd,
            "is_stale":       cache.is_stale,
            "next_sync_in":   round(next_sync_h, 2) if not cache.is_stale else 0.0,
        }

    def invalidate(self) -> None:
        """
        Wipe the in-memory cache. The disk file is kept but will be reloaded.
        Used after a forced Plaid sync to ensure next get_holdings() is fresh.
        """
        self._memory_cache = None
        logger.debug("In-memory holdings cache invalidated")

    def delete_cache_file(self) -> bool:
        """
        Delete holdings_cache.json from disk.
        Returns True if deleted, False if it didn't exist.
        """
        self.invalidate()
        if self._cache_path.exists():
            self._cache_path.unlink()
            logger.info("Deleted holdings cache: %s", self._cache_path)
            return True
        return False

    # ── Plaid fetch ───────────────────────────────────────────────────────────

    def _fetch_from_plaid(
        self,
        account_ids: Optional[list[str]] = None,
    ) -> HoldingsCache:
        """
        Call Plaid investments/holdings/get and convert to HoldingsCache.
        Lazy-creates PlaidClient on first call.
        """
        from plaid_client import PlaidClient, normalise_ticker

        if self._plaid is None:
            self._plaid = PlaidClient()

        plaid_portfolio = self._plaid.get_holdings(account_ids=account_ids)

        holdings: list[CachedHolding] = []
        for h in plaid_portfolio.holdings:
            # Ticker normalisation happens in PlaidClient already, but double-check
            ticker = normalise_ticker(h.ticker) if h.ticker else ""
            if not ticker:
                logger.warning("Holding with empty ticker skipped (security_id may be unlisted)")
                continue

            holdings.append(CachedHolding(
                ticker            = ticker,
                quantity          = h.quantity,
                cost_basis        = h.cost_basis,
                institution_price = h.institution_price,
                security_type     = h.security_type,
                name              = h.name,
                account_id        = h.account_id,
            ))

        return HoldingsCache(
            last_synced = datetime.now(tz=timezone.utc).isoformat(),
            account_ids = plaid_portfolio.account_ids,
            cash_usd    = plaid_portfolio.cash_usd,
            holdings    = holdings,
        )

    # ── Cache I/O ─────────────────────────────────────────────────────────────

    def _write_cache(self, cache: HoldingsCache) -> None:
        """Persist HoldingsCache to holdings_cache.json."""
        try:
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(cache.to_dict(), f, indent=2)
            logger.debug("Holdings cache written: %s (%d holdings)", self._cache_path, len(cache.holdings))
        except OSError as exc:
            logger.error("Failed to write holdings cache: %s", exc)

    def _read_cache(self) -> Optional[HoldingsCache]:
        """Load HoldingsCache from disk. Returns None if file missing or corrupt."""
        if not self._cache_path.exists():
            return None
        try:
            with open(self._cache_path, encoding="utf-8") as f:
                data = json.load(f)
            cache = HoldingsCache.from_dict(data)
            return cache
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error("Holdings cache corrupt (%s) — will re-fetch from Plaid", exc)
            return None
