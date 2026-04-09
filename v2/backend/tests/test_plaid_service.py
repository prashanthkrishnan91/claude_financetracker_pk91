"""Tests for the Plaid sync service — SyncResult, SyncStatus, ticker normalization."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.plaid_service import (
    SyncResult,
    SyncStatus,
    _PLAID_TICKER_MAP,
    _CACHE_TTL_HOURS,
)


class TestSyncResult:
    def test_success(self):
        r = SyncResult(
            status="success",
            message="Synced 39 holdings from Robinhood",
            holdings_count=39,
            cash_balance=1042.17,
            positions_updated=3,
            positions_created=2,
            synced_at=datetime.now(timezone.utc),
            duration_ms=1230,
        )
        assert r.status == "success"
        assert r.holdings_count == 39

    def test_cached(self):
        r = SyncResult(
            status="cached",
            message="Synced 2.3h ago — use force=true to re-sync",
        )
        assert r.status == "cached"
        assert r.holdings_count == 0

    def test_error(self):
        r = SyncResult(status="error", message="Plaid API error: timeout")
        assert r.status == "error"


class TestSyncStatus:
    def test_fresh_status(self):
        s = SyncStatus(
            synced_at=datetime.now(timezone.utc),
            holdings_count=39,
            cash_balance=1042.17,
            status="success",
            age_hours=2.5,
        )
        assert s.is_fresh

    def test_stale_status(self):
        s = SyncStatus(age_hours=25.0)
        assert not s.is_fresh

    def test_default_never_synced(self):
        s = SyncStatus()
        assert s.synced_at is None
        assert s.status == "never_synced"
        assert not s.is_fresh

    def test_cache_ttl_boundary(self):
        """Exactly at TTL should not be fresh."""
        s = SyncStatus(age_hours=_CACHE_TTL_HOURS)
        assert not s.is_fresh

    def test_just_under_ttl(self):
        s = SyncStatus(synced_at=datetime.now(timezone.utc), age_hours=_CACHE_TTL_HOURS - 0.1)
        assert s.is_fresh


class TestPlaidTickerNormalization:
    def test_berkshire_normalization(self):
        assert _PLAID_TICKER_MAP.get("BRK.B") == "BRK-B"
        assert _PLAID_TICKER_MAP.get("BRK.A") == "BRK-A"

    def test_brown_forman_normalization(self):
        assert _PLAID_TICKER_MAP.get("BF.B") == "BF-B"
        assert _PLAID_TICKER_MAP.get("BF.A") == "BF-A"

    def test_normal_ticker_not_mapped(self):
        """Regular tickers should not be in the map."""
        assert _PLAID_TICKER_MAP.get("NVDA") is None
        assert _PLAID_TICKER_MAP.get("AAPL") is None
