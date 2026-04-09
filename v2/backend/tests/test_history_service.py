"""Tests for the history service — HistoryPoint, cache freshness, batch."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.history_service import HistoryPoint, HistoryService


class TestHistoryPoint:
    def test_basic_construction(self):
        hp = HistoryPoint(
            date="2026-04-07", open=870.0, high=880.0,
            low=865.0, close=875.22, volume=45000000,
        )
        assert hp.close == 875.22
        assert hp.date == "2026-04-07"

    def test_to_dict(self):
        hp = HistoryPoint(
            date="2026-04-07", open=870.0, high=880.0,
            low=865.0, close=875.22, volume=45000000,
        )
        d = hp.to_dict()
        assert d["price_date"] == "2026-04-07"
        assert d["close_price"] == 875.22
        assert d["volume"] == 45000000
        assert "open_price" in d
        assert "high_price" in d
        assert "low_price" in d


class TestCacheFreshness:
    def test_today_is_fresh(self):
        points = [HistoryPoint(
            date=date.today().isoformat(), open=1, high=2,
            low=0.5, close=1.5, volume=100,
        )]
        assert HistoryService._cache_is_fresh(points)

    def test_yesterday_is_fresh(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        points = [HistoryPoint(
            date=yesterday, open=1, high=2, low=0.5, close=1.5, volume=100,
        )]
        assert HistoryService._cache_is_fresh(points)

    def test_weekend_friday_is_fresh(self):
        """Friday data should be fresh over the weekend (within 3 days)."""
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        points = [HistoryPoint(
            date=three_days_ago, open=1, high=2, low=0.5, close=1.5, volume=100,
        )]
        assert HistoryService._cache_is_fresh(points)

    def test_old_data_is_stale(self):
        old = (date.today() - timedelta(days=10)).isoformat()
        points = [HistoryPoint(
            date=old, open=1, high=2, low=0.5, close=1.5, volume=100,
        )]
        assert not HistoryService._cache_is_fresh(points)

    def test_empty_list_is_not_fresh(self):
        assert not HistoryService._cache_is_fresh([])

    def test_uses_last_point(self):
        """Should check freshness of the LAST point (most recent date)."""
        old = (date.today() - timedelta(days=30)).isoformat()
        recent = date.today().isoformat()
        points = [
            HistoryPoint(date=old, open=1, high=2, low=0.5, close=1.5, volume=100),
            HistoryPoint(date=recent, open=1, high=2, low=0.5, close=1.5, volume=100),
        ]
        assert HistoryService._cache_is_fresh(points)


class TestHistoryServiceInit:
    def test_init_no_supabase(self):
        svc = HistoryService()
        assert svc._supabase is None
        assert svc._http is None

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        svc = HistoryService()
        await svc.close()
        await svc.close()  # Double close should not raise
