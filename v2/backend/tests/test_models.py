"""Tests for Pydantic models — validation, serialization, edge cases."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.user import UserCreate, UserUpdate, UserResponse
from app.models.position import PositionCreate, PositionUpdate, PositionWithPrice
from app.models.portfolio import SnapshotCreate, PortfolioSummary, TargetAllocationCreate
from app.models.transaction import TransactionCreate, TransactionImportResult
from app.models.recommendation import RecommendationBase, InsightCard, DecisionLogCreate
from app.models.price import PriceQuote, PriceHistoryPoint, BatchPriceRequest
from app.models.deposit import DepositPlanCreate, DepositSchedule


class TestUserModels:
    def test_user_create_valid(self):
        user = UserCreate(email="test@example.com", password="securepass123")
        assert user.email == "test@example.com"
        assert user.deposit_amount == 900.00
        assert user.deposit_frequency == "biweekly"
        assert user.theme == "dark"

    def test_user_create_invalid_email(self):
        with pytest.raises(Exception):
            UserCreate(email="not-an-email", password="securepass123")

    def test_user_create_short_password(self):
        with pytest.raises(Exception):
            UserCreate(email="test@example.com", password="short")

    def test_user_update_partial(self):
        update = UserUpdate(display_name="New Name")
        dumped = update.model_dump(exclude_unset=True)
        assert dumped == {"display_name": "New Name"}
        assert "deposit_amount" not in dumped

    def test_user_create_custom_settings(self):
        user = UserCreate(
            email="test@example.com",
            password="securepass123",
            deposit_amount=1200.00,
            deposit_frequency="monthly",
            theme="light",
        )
        assert user.deposit_amount == 1200.00
        assert user.deposit_frequency == "monthly"
        assert user.theme == "light"


class TestPositionModels:
    def test_position_create_stock(self):
        pos = PositionCreate(
            ticker="NVDA", name="NVIDIA", category="Core",
            shares=Decimal("35.5042"), avg_cost=Decimal("116.02"),
            lt_eligible=True,
        )
        assert pos.ticker == "NVDA"
        assert pos.shares == Decimal("35.5042")
        assert pos.source == "manual"

    def test_position_create_crypto(self):
        pos = PositionCreate(
            ticker="BTC", name="Bitcoin", category="Crypto",
            shares=Decimal("0.03433"), avg_cost=Decimal("66997.0"),
            coingecko_id="bitcoin", lt_eligible=True,
        )
        assert pos.coingecko_id == "bitcoin"

    def test_position_create_invalid_category(self):
        with pytest.raises(Exception):
            PositionCreate(
                ticker="XXX", name="Bad", category="InvalidCategory",
                shares=Decimal("1"),
            )

    def test_position_update_partial(self):
        update = PositionUpdate(shares=Decimal("10.5"))
        dumped = update.model_dump(exclude_unset=True)
        assert dumped == {"shares": Decimal("10.5")}

    def test_position_with_price(self):
        pos = PositionWithPrice(
            id=uuid4(), user_id=uuid4(), ticker="AAPL", name="Apple",
            category="Core", shares=Decimal("16.1136"), avg_cost=Decimal("213.03"),
            source="manual", created_at=datetime.now(), updated_at=datetime.now(),
            current_price=230.50, market_value=3716.90,
            unrealised_pnl=280.42, unrealised_pnl_pct=8.17,
        )
        assert pos.current_price == 230.50


class TestPortfolioModels:
    def test_snapshot_create(self):
        snap = SnapshotCreate(
            total_equity=Decimal("45000.00"),
            total_cost=Decimal("38000.00"),
            total_pnl=Decimal("7000.00"),
            total_pnl_pct=Decimal("18.42"),
        )
        assert snap.total_equity == Decimal("45000.00")

    def test_target_allocation_valid(self):
        target = TargetAllocationCreate(ticker="VOO", target_pct=Decimal("22.5"))
        assert target.target_pct == Decimal("22.5")

    def test_target_allocation_out_of_range(self):
        with pytest.raises(Exception):
            TargetAllocationCreate(ticker="VOO", target_pct=Decimal("150"))

    def test_portfolio_summary(self):
        summary = PortfolioSummary(
            total_equity=50000, total_cost=40000, total_pnl=10000,
            total_pnl_pct=25.0, cash_balance=1042.17,
            day_change=150.50, day_change_pct=0.3,
            stocks_value=25000, etfs_value=22000, crypto_value=3000,
            positions_count=39, prices_fresh=35, prices_stale=4,
        )
        assert summary.positions_count == 39


class TestTransactionModels:
    def test_transaction_create_buy(self):
        tx = TransactionCreate(
            ticker="NVDA", tx_type="Buy",
            quantity=Decimal("10"), price=Decimal("116.02"),
            amount=Decimal("1160.20"), tx_date=date(2025, 3, 15),
        )
        assert tx.tx_type == "Buy"

    def test_transaction_create_invalid_type(self):
        with pytest.raises(Exception):
            TransactionCreate(
                ticker="NVDA", tx_type="InvalidType",
                tx_date=date(2025, 3, 15),
            )

    def test_import_result(self):
        result = TransactionImportResult(
            total_rows=583, new_rows=0, duplicates_skipped=583, errors=0,
        )
        assert result.duplicates_skipped == 583


class TestRecommendationModels:
    def test_recommendation_base(self):
        rec = RecommendationBase(
            ticker="GLD", action="TRIM",
            detail="Trim 25% near $450 target",
            rationale="Gold at all-time highs, take partial profits",
            urgency=3,
        )
        assert rec.action == "TRIM"
        assert rec.urgency == 3

    def test_insight_card(self):
        card = InsightCard(
            id=uuid4(), ticker="NVDA", name="NVIDIA",
            action="HOLD", detail="Strong momentum, hold for LT",
            rationale="AI capex cycle intact, 2026 guidance strong",
            urgency=1, color="blue",
            tax_note="LT eligible", drip_note="",
            current_price=875.22, pnl_pct=654.0,
            category="Core",
        )
        assert card.color == "blue"

    def test_decision_log_create(self):
        entry = DecisionLogCreate(
            ticker="VTV", decision="accepted",
            notes="Sold full position, reinvested into VOO",
        )
        assert entry.decision == "accepted"


class TestPriceModels:
    def test_price_quote_valid(self):
        quote = PriceQuote(
            ticker="NVDA", mid_price=875.22,
            bid=875.00, ask=875.44,
            last_trade=875.15, source="finnhub",
            timestamp=1712000000.0,
        )
        assert quote.is_valid

    def test_price_quote_invalid(self):
        quote = PriceQuote(
            ticker="FAIL", mid_price=0,
            last_trade=0, source="error",
            timestamp=1712000000.0,
            error="API timeout",
        )
        assert not quote.is_valid

    def test_batch_request_validation(self):
        req = BatchPriceRequest(tickers=["NVDA", "AAPL", "BTC"])
        assert len(req.tickers) == 3

    def test_batch_request_empty(self):
        with pytest.raises(Exception):
            BatchPriceRequest(tickers=[])

    def test_price_history_point(self):
        point = PriceHistoryPoint(
            price_date=date(2026, 4, 7),
            close_price=875.22,
            volume=45000000,
        )
        assert point.close_price == 875.22


class TestDepositModels:
    def test_deposit_plan_create(self):
        plan = DepositPlanCreate(
            deposit_date=date(2026, 4, 17),
            amount=Decimal("900.00"),
            allocation={"NVDA": 252.0, "VOO": 198.0, "VYM": 153.0, "QQQ": 153.0, "GOOGL": 144.0},
            rotating_pick="GOOGL",
        )
        assert sum(plan.allocation.values()) == 900.0
