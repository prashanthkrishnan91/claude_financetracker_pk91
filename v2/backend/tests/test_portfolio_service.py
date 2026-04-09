"""Tests for portfolio service — summary computation, category breakdowns."""

from __future__ import annotations

import pytest

from app.models.portfolio import PortfolioSummary


class TestPortfolioSummary:
    def test_basic_summary(self):
        s = PortfolioSummary(
            total_equity=50000, total_cost=40000, total_pnl=10000,
            total_pnl_pct=25.0, cash_balance=1042.17,
            day_change=150.50, day_change_pct=0.3,
            stocks_value=25000, etfs_value=22000, crypto_value=3000,
            positions_count=39, prices_fresh=35, prices_stale=4,
        )
        assert s.total_equity == 50000
        assert s.total_pnl_pct == 25.0
        assert s.positions_count == 39

    def test_empty_portfolio(self):
        s = PortfolioSummary(
            total_equity=0, total_cost=0, total_pnl=0, total_pnl_pct=0,
            cash_balance=0, day_change=0, day_change_pct=0,
            stocks_value=0, etfs_value=0, crypto_value=0,
            positions_count=0, prices_fresh=0, prices_stale=0,
        )
        assert s.positions_count == 0
        assert s.total_equity == 0

    def test_negative_pnl(self):
        s = PortfolioSummary(
            total_equity=35000, total_cost=40000, total_pnl=-5000,
            total_pnl_pct=-12.5, cash_balance=500,
            day_change=-200, day_change_pct=-0.57,
            stocks_value=20000, etfs_value=13000, crypto_value=2000,
            positions_count=15, prices_fresh=10, prices_stale=5,
        )
        assert s.total_pnl < 0
        assert s.total_pnl_pct < 0

    def test_category_breakdown_sums(self):
        """stocks + etfs + crypto should approximately equal total_equity - cash."""
        stocks, etfs, crypto = 25000, 22000, 3000
        total_equity = stocks + etfs + crypto + 1042.17  # + cash
        s = PortfolioSummary(
            total_equity=total_equity, total_cost=40000,
            total_pnl=total_equity - 40000, total_pnl_pct=0,
            cash_balance=1042.17, day_change=0, day_change_pct=0,
            stocks_value=stocks, etfs_value=etfs, crypto_value=crypto,
            positions_count=39, prices_fresh=39, prices_stale=0,
        )
        assert s.stocks_value + s.etfs_value + s.crypto_value == pytest.approx(
            s.total_equity - s.cash_balance
        )
