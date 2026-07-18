"""Tax-lot engine — event classification, FIFO, anniversary logic, reconciliation gating."""

from __future__ import annotations

from datetime import date

from app.services import tax_lot_engine as tle


def _tx(tx_type, qty=None, price=None, tx_date="2025-01-10", ticker="VTI", amount=None):
    return {
        "ticker": ticker, "tx_type": tx_type, "quantity": qty,
        "price": price, "amount": amount, "tx_date": tx_date,
    }


AS_OF = date(2026, 7, 1)


# ── Classification: every production tx_type is explicitly classified ─────────


class TestClassification:
    def test_buy_and_sell(self):
        assert tle.classify_transaction(_tx("Buy", 10, 100)) == tle.SHARE_INCREASING
        assert tle.classify_transaction(_tx("Sell", 5, 100)) == tle.SHARE_DECREASING

    def test_drip_with_price_is_share_increasing(self):
        assert tle.classify_transaction(_tx("DRIP", 0.5, 100)) == tle.SHARE_INCREASING

    def test_drip_with_shares_but_no_price_is_unsupported(self):
        assert tle.classify_transaction(_tx("DRIP", 0.5, None)) == tle.UNSUPPORTED_UNKNOWN

    def test_drip_without_shares_is_non_share_affecting(self):
        assert tle.classify_transaction(_tx("DRIP", None, None, amount=12.5)) == tle.NON_SHARE_AFFECTING

    def test_split_with_share_movement_is_unsupported(self):
        assert tle.classify_transaction(_tx("SPL", 10)) == tle.UNSUPPORTED_UNKNOWN

    def test_cash_types_without_shares_are_non_share_affecting(self):
        for t in ("CDIV", "ACH", "RTP"):
            assert tle.classify_transaction(_tx(t, None, None, amount=100)) == tle.NON_SHARE_AFFECTING

    def test_cash_types_with_shares_are_never_silently_ignored(self):
        for t in ("CDIV", "ACH", "RTP"):
            assert tle.classify_transaction(_tx(t, 3)) == tle.UNSUPPORTED_UNKNOWN

    def test_other_and_unknown_with_shares_are_unsupported(self):
        assert tle.classify_transaction(_tx("Other", 4)) == tle.UNSUPPORTED_UNKNOWN
        assert tle.classify_transaction(_tx("TransferIn", 4)) == tle.UNSUPPORTED_UNKNOWN

    def test_other_without_shares_is_non_share_affecting(self):
        assert tle.classify_transaction(_tx("Other", None)) == tle.NON_SHARE_AFFECTING

    def test_zero_quantity_buy_does_not_create_shares(self):
        assert tle.classify_transaction(_tx("Buy", 0)) == tle.NON_SHARE_AFFECTING


# ── FIFO ledger ───────────────────────────────────────────────────────────────


class TestFifoLedger:
    def test_buys_create_lots_and_sells_deplete_fifo(self):
        ledger = tle.build_ticker_ledger([
            _tx("Buy", 10, 100, "2025-01-10"),
            _tx("Buy", 10, 120, "2025-03-10"),
            _tx("Sell", 12, 130, "2025-05-01"),
        ], as_of=AS_OF)["VTI"]
        lots = ledger["open_lots"]
        assert len(lots) == 1
        assert lots[0]["acquired"] == date(2025, 3, 10)
        assert round(lots[0]["quantity"], 6) == 8.0
        assert ledger["lot_shares"] == 8.0
        assert not ledger["oversold"]

    def test_drip_creates_a_lot(self):
        ledger = tle.build_ticker_ledger([
            _tx("Buy", 10, 100, "2024-01-10"),
            _tx("DRIP", 0.25, 110, "2024-06-10"),
        ], as_of=AS_OF)["VTI"]
        assert len(ledger["open_lots"]) == 2
        assert ledger["open_lots"][1]["source_tx_type"] == "DRIP"

    def test_oversell_marks_ledger_oversold(self):
        ledger = tle.build_ticker_ledger([
            _tx("Buy", 5, 100, "2025-01-10"),
            _tx("Sell", 8, 100, "2025-02-10"),
        ], as_of=AS_OF)["VTI"]
        assert ledger["oversold"] is True

    def test_unsupported_events_recorded_not_dropped(self):
        ledger = tle.build_ticker_ledger([
            _tx("Buy", 10, 100, "2025-01-10"),
            _tx("SPL", 10, None, "2025-02-10"),
        ], as_of=AS_OF)["VTI"]
        assert len(ledger["unsupported_events"]) == 1
        assert ledger["unsupported_events"][0]["tx_type"] == "SPL"
        assert ledger["unsupported_events"][0]["reason"] == "share_affecting_event_not_modelable"

    def test_share_affecting_event_without_date_is_unsupported(self):
        ledger = tle.build_ticker_ledger([
            {"ticker": "VTI", "tx_type": "Buy", "quantity": 10, "price": 100, "tx_date": None},
        ], as_of=AS_OF)["VTI"]
        assert ledger["open_lots"] == []
        assert ledger["unsupported_events"][0]["reason"] == "share_affecting_event_missing_date"


# ── Calendar anniversary logic ────────────────────────────────────────────────


class TestLongTermAnniversary:
    def test_long_term_starts_day_after_calendar_anniversary(self):
        assert tle.long_term_start_date(date(2025, 3, 10)) == date(2026, 3, 11)

    def test_not_a_365_day_shortcut_across_leap_year(self):
        # 2024 is a leap year: 365 days after 2024-03-01 is 2025-03-01, but the
        # calendar anniversary is also 2025-03-01 → long-term starts 03-02.
        assert tle.long_term_start_date(date(2024, 3, 1)) == date(2025, 3, 2)
        # Acquired 2023-03-01 (non-leap year span includes leap day):
        # anniversary 2024-03-01, long-term starts 2024-03-02 — even though
        # that is 367 days, not 365.
        assert tle.long_term_start_date(date(2023, 3, 1)) == date(2024, 3, 2)

    def test_feb_29_acquisition_uses_mar_1(self):
        assert tle.long_term_anniversary(date(2024, 2, 29)) == date(2025, 3, 1)
        assert tle.long_term_start_date(date(2024, 2, 29)) == date(2025, 3, 2)

    def test_presentation_classifies_short_and_long_term(self):
        ledger = tle.build_ticker_ledger([
            _tx("Buy", 10, 100, "2025-01-10"),   # long-term by 2026-07-01
            _tx("Buy", 5, 120, "2025-12-01"),    # short-term at 2026-07-01
        ], as_of=AS_OF)["VTI"]
        rows = tle.present_lots(ledger, 130.0, as_of=AS_OF)
        assert rows[0]["estimated_holding_classification"] == "long_term"
        assert rows[0]["days_until_long_term"] == 0
        assert rows[1]["estimated_holding_classification"] == "short_term"
        assert rows[1]["estimated_long_term_start_date"] == "2026-12-02"
        assert rows[1]["days_until_long_term"] == (date(2026, 12, 2) - AS_OF).days


# ── Reconciliation gating ─────────────────────────────────────────────────────


class TestReconciliation:
    def _ledger(self, *txs):
        return tle.build_ticker_ledger(list(txs), as_of=AS_OF)["VTI"]

    def test_reconciled_when_shares_and_basis_match(self):
        ledger = self._ledger(_tx("Buy", 10, 100, "2025-01-10"))
        recon = tle.reconcile_ledger(ledger, position_shares=10.0, position_cost_basis=1000.0)
        assert recon["status"] == tle.STATUS_RECONCILED

    def test_quantity_mismatch_blocks(self):
        ledger = self._ledger(_tx("Buy", 10, 100, "2025-01-10"))
        recon = tle.reconcile_ledger(ledger, position_shares=12.0, position_cost_basis=1200.0)
        assert recon["status"] == tle.STATUS_QUANTITY_MISMATCH

    def test_basis_mismatch_blocks(self):
        ledger = self._ledger(_tx("Buy", 10, 100, "2025-01-10"))
        recon = tle.reconcile_ledger(ledger, position_shares=10.0, position_cost_basis=1500.0)
        assert recon["status"] == tle.STATUS_BASIS_MISMATCH
        assert recon["basis_difference_pct"] is not None

    def test_basis_within_2pct_tolerance_passes(self):
        ledger = self._ledger(_tx("Buy", 10, 100, "2025-01-10"))
        recon = tle.reconcile_ledger(ledger, position_shares=10.0, position_cost_basis=1015.0)
        assert recon["status"] == tle.STATUS_RECONCILED

    def test_unsupported_events_block_before_anything_else(self):
        ledger = self._ledger(
            _tx("Buy", 10, 100, "2025-01-10"),
            _tx("SPL", 20, None, "2025-02-01"),
        )
        recon = tle.reconcile_ledger(ledger, position_shares=30.0, position_cost_basis=None)
        assert recon["status"] == tle.STATUS_BLOCKED_UNSUPPORTED

    def test_oversold_ledger_blocks(self):
        ledger = self._ledger(
            _tx("Buy", 5, 100, "2025-01-10"),
            _tx("Sell", 9, 110, "2025-02-01"),
        )
        recon = tle.reconcile_ledger(ledger, position_shares=0.0, position_cost_basis=None)
        assert recon["status"] == tle.STATUS_OVERSOLD

    def test_fractional_share_tolerance(self):
        ledger = self._ledger(_tx("Buy", 10.000049, 100, "2025-01-10"))
        recon = tle.reconcile_ledger(ledger, position_shares=10.0, position_cost_basis=1000.0)
        assert recon["status"] == tle.STATUS_RECONCILED


# ── Presentation safety ───────────────────────────────────────────────────────


class TestPresentationSafety:
    def test_no_price_gives_null_market_fields(self):
        ledger = tle.build_ticker_ledger([_tx("Buy", 10, 100, "2025-01-10")], as_of=AS_OF)["VTI"]
        rows = tle.present_lots(ledger, None, as_of=AS_OF)
        assert rows[0]["current_value"] is None
        assert rows[0]["unrealized_gain"] is None

    def test_no_tax_dollar_estimate_fields_exist(self):
        ledger = tle.build_ticker_ledger([_tx("Buy", 10, 100, "2025-01-10")], as_of=AS_OF)["VTI"]
        rows = tle.present_lots(ledger, 130.0, as_of=AS_OF)
        joined = " ".join(rows[0].keys())
        assert "tax_rate" not in joined
        assert "estimated_tax" not in joined
        assert "tax_if_sold" not in joined

    def test_engine_module_has_no_tax_advice_language(self):
        import inspect
        src = inspect.getsource(tle).lower()
        assert "not tax advice" in src
        assert "estimates" in src
        assert "optimiz" not in src  # no tax-optimization claims

    def test_market_math_when_price_available(self):
        ledger = tle.build_ticker_ledger([_tx("Buy", 10, 100, "2025-01-10")], as_of=AS_OF)["VTI"]
        rows = tle.present_lots(ledger, 130.0, as_of=AS_OF)
        assert rows[0]["current_value"] == 1300.0
        assert rows[0]["unrealized_gain"] == 300.0
        assert rows[0]["unrealized_gain_pct"] == 30.0


class TestZeroPriceBuyFailsClosed:
    def test_buy_with_shares_but_no_price_is_unsupported(self):
        assert tle.classify_transaction(_tx("Buy", 10, None)) == tle.UNSUPPORTED_UNKNOWN
        assert tle.classify_transaction(_tx("Buy", 10, 0)) == tle.UNSUPPORTED_UNKNOWN

    def test_zero_price_buy_blocks_reconciliation_not_fake_gain(self):
        ledger = tle.build_ticker_ledger(
            [_tx("Buy", 10, None, "2025-01-10")], as_of=AS_OF
        )["VTI"]
        assert ledger["open_lots"] == []
        assert ledger["unsupported_events"][0]["tx_type"] == "Buy"
        recon = tle.reconcile_ledger(ledger, position_shares=10.0, position_cost_basis=None)
        assert recon["status"] == tle.STATUS_BLOCKED_UNSUPPORTED
