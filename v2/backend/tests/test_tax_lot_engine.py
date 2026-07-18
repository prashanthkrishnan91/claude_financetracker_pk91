"""Tax lot engine — pure-function tests (FIFO lots, ST/LT status, countdown, tax)."""

from __future__ import annotations

from datetime import date

from app.services.tax_lot_engine import (
    build_tax_lots,
    enrich_lots_with_market,
    summarize_ticker_lots,
)

AS_OF = date(2026, 7, 18)


def _tx(ticker, tx_type, qty, price, tx_date):
    return {
        "ticker": ticker,
        "tx_type": tx_type,
        "quantity": qty,
        "price": price,
        "tx_date": tx_date,
    }


class TestBuildTaxLots:
    def test_single_buy_creates_one_lot(self):
        lots = build_tax_lots([_tx("AAPL", "Buy", 10, 150.0, "2026-01-10")], as_of=AS_OF)
        assert list(lots) == ["AAPL"]
        lot = lots["AAPL"][0]
        assert lot["quantity"] == 10
        assert lot["cost_per_share"] == 150.0
        assert lot["cost_basis"] == 1500.0

    def test_short_term_lot_has_countdown(self):
        lots = build_tax_lots([_tx("AAPL", "Buy", 10, 150.0, "2026-01-10")], as_of=AS_OF)
        lot = lots["AAPL"][0]
        assert lot["is_long_term"] is False
        held = (AS_OF - date(2026, 1, 10)).days
        assert lot["holding_days"] == held
        assert lot["days_until_long_term"] == 365 - held
        assert lot["long_term_date"] == "2027-01-10"

    def test_long_term_lot_countdown_zero(self):
        lots = build_tax_lots([_tx("VOO", "Buy", 5, 400.0, "2024-03-01")], as_of=AS_OF)
        lot = lots["VOO"][0]
        assert lot["is_long_term"] is True
        assert lot["days_until_long_term"] == 0

    def test_exact_365_day_boundary_is_long_term(self):
        lots = build_tax_lots([_tx("MSFT", "Buy", 1, 100.0, "2025-07-18")], as_of=AS_OF)
        lot = lots["MSFT"][0]
        assert lot["holding_days"] == 365
        assert lot["is_long_term"] is True

    def test_day_364_is_short_term(self):
        lots = build_tax_lots([_tx("MSFT", "Buy", 1, 100.0, "2025-07-19")], as_of=AS_OF)
        lot = lots["MSFT"][0]
        assert lot["is_long_term"] is False
        assert lot["days_until_long_term"] == 1

    def test_sell_depletes_fifo_oldest_first(self):
        lots = build_tax_lots([
            _tx("NVDA", "Buy", 10, 100.0, "2025-01-01"),
            _tx("NVDA", "Buy", 10, 200.0, "2026-01-01"),
            _tx("NVDA", "Sell", 12, 250.0, "2026-06-01"),
        ], as_of=AS_OF)
        remaining = lots["NVDA"]
        assert len(remaining) == 1
        # Oldest lot (100.0) fully consumed; 2 shares taken from second lot.
        assert remaining[0]["cost_per_share"] == 200.0
        assert remaining[0]["quantity"] == 8

    def test_full_liquidation_removes_ticker(self):
        lots = build_tax_lots([
            _tx("TSLA", "Buy", 5, 200.0, "2025-01-01"),
            _tx("TSLA", "Sell", 5, 300.0, "2026-01-01"),
        ], as_of=AS_OF)
        assert "TSLA" not in lots

    def test_non_trade_rows_ignored(self):
        lots = build_tax_lots([
            _tx("VYM", "CDIV", 0, 0, "2026-01-01"),
            _tx("VYM", "DRIP", 0, 0, "2026-01-01"),
            _tx(None, "Buy", 5, 10.0, "2026-01-01"),
        ], as_of=AS_OF)
        assert lots == {}

    def test_unparseable_date_skipped(self):
        lots = build_tax_lots([_tx("AAPL", "Buy", 1, 1.0, "not-a-date")], as_of=AS_OF)
        assert lots == {}


class TestEnrichAndSummarize:
    def _lots(self):
        return build_tax_lots([
            _tx("AAPL", "Buy", 10, 100.0, "2024-01-10"),   # long-term
            _tx("AAPL", "Buy", 10, 200.0, "2026-05-01"),   # short-term
        ], as_of=AS_OF)

    def test_gain_and_tax_rates_per_lot(self):
        enriched = enrich_lots_with_market(
            self._lots(), {"AAPL": 250.0},
            short_term_rate=0.32, long_term_rate=0.15,
        )
        lt, st = enriched["AAPL"]
        assert lt["is_long_term"] and not st["is_long_term"]
        assert lt["unrealized_gain"] == 1500.0
        assert lt["tax_rate_applied"] == 0.15
        assert lt["estimated_tax_if_sold"] == 225.0
        assert st["unrealized_gain"] == 500.0
        assert st["tax_rate_applied"] == 0.32
        assert st["estimated_tax_if_sold"] == 160.0

    def test_missing_price_yields_null_market_fields(self):
        enriched = enrich_lots_with_market(
            self._lots(), {}, short_term_rate=0.32, long_term_rate=0.15,
        )
        for lot in enriched["AAPL"]:
            assert lot["current_price"] is None
            assert lot["unrealized_gain"] is None
            assert lot["estimated_tax_if_sold"] is None

    def test_summary_counts_and_countdown(self):
        enriched = enrich_lots_with_market(
            self._lots(), {"AAPL": 250.0},
            short_term_rate=0.32, long_term_rate=0.15,
        )
        s = summarize_ticker_lots(enriched["AAPL"])
        assert s["lot_count"] == 2
        assert s["short_term_lot_count"] == 1
        assert s["long_term_lot_count"] == 1
        assert s["total_cost_basis"] == 3000.0
        assert s["unrealized_gain_total"] == 2000.0
        expected_countdown = 365 - (AS_OF - date(2026, 5, 1)).days
        assert s["next_long_term_countdown_days"] == expected_countdown

    def test_summary_all_long_term_countdown_zero(self):
        lots = build_tax_lots([_tx("VOO", "Buy", 1, 1.0, "2020-01-01")], as_of=AS_OF)
        enriched = enrich_lots_with_market(lots, {}, short_term_rate=0.32, long_term_rate=0.15)
        s = summarize_ticker_lots(enriched["VOO"])
        assert s["next_long_term_countdown_days"] == 0
        assert s["unrealized_gain_total"] is None
