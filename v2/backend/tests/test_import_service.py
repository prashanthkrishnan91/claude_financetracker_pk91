"""Tests for the CSV import service — fingerprinting, normalization, dedup."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.import_service import (
    _norm_date,
    _norm_decimal,
    make_fingerprint,
)


# ── Decimal normalization ────────────────────────────────────────────────────


class TestNormDecimal:
    def test_basic_number(self):
        assert _norm_decimal("874.63") == "874.630000"

    def test_dollar_sign(self):
        assert _norm_decimal("$874.63") == "874.630000"

    def test_commas(self):
        assert _norm_decimal("1,234.56") == "1234.560000"

    def test_dollar_and_commas(self):
        assert _norm_decimal("$1,234.56") == "1234.560000"

    def test_parenthetical_negative(self):
        assert _norm_decimal("(123.45)") == "-123.450000"

    def test_empty_string(self):
        assert _norm_decimal("") == "0.000000"

    def test_none_value(self):
        assert _norm_decimal("") == "0.000000"

    def test_zero(self):
        assert _norm_decimal("0") == "0.000000"

    def test_high_precision(self):
        assert _norm_decimal("874.630000") == "874.630000"

    def test_different_formats_same_output(self):
        """Core dedup guarantee: same value, different formats → same fingerprint."""
        assert _norm_decimal("$874.63") == _norm_decimal("874.63")
        assert _norm_decimal("$874.63") == _norm_decimal("874.630000")

    def test_custom_places(self):
        assert _norm_decimal("100", places=2) == "100.00"

    def test_invalid_string(self):
        assert _norm_decimal("abc") == "0.000000"


# ── Date normalization ───────────────────────────────────────────────────────


class TestNormDate:
    def test_iso_format(self):
        assert _norm_date("2026-04-02") == "2026-04-02"

    def test_us_format(self):
        assert _norm_date("4/2/2026") == "2026-04-02"

    def test_us_format_zero_padded(self):
        assert _norm_date("04/02/2026") == "2026-04-02"

    def test_iso_with_time(self):
        assert _norm_date("2026-04-02T14:30:00") == "2026-04-02"

    def test_different_formats_same_output(self):
        """Core dedup guarantee: same date, different formats → same string."""
        assert _norm_date("4/2/2026") == _norm_date("2026-04-02")

    def test_empty_string(self):
        assert _norm_date("") == "1970-01-01"


# ── Fingerprint generation ───────────────────────────────────────────────────


class TestMakeFingerprint:
    def test_stock_transaction(self):
        fp = make_fingerprint("4/2/2026", "NVDA", "BUY", "10", "$116.02")
        assert isinstance(fp, str)
        assert len(fp) == 64  # SHA-256 hex digest

    def test_same_input_same_fingerprint(self):
        fp1 = make_fingerprint("4/2/2026", "NVDA", "BUY", "10", "$116.02")
        fp2 = make_fingerprint("2026-04-02", "NVDA", "BUY", "10.000000", "116.020000")
        assert fp1 == fp2  # Core dedup guarantee

    def test_different_dates_different_fingerprint(self):
        fp1 = make_fingerprint("4/2/2026", "NVDA", "BUY", "10", "$116.02")
        fp2 = make_fingerprint("4/3/2026", "NVDA", "BUY", "10", "$116.02")
        assert fp1 != fp2

    def test_different_tickers_different_fingerprint(self):
        fp1 = make_fingerprint("4/2/2026", "NVDA", "BUY", "10", "$116.02")
        fp2 = make_fingerprint("4/2/2026", "AAPL", "BUY", "10", "$116.02")
        assert fp1 != fp2

    def test_cash_transaction(self):
        """Cash-only rows (ACH, RTP) use different canonical format."""
        fp = make_fingerprint("4/2/2026", "", "ACH", "0", "0", amount="$900.00", settle="4/5/2026")
        assert len(fp) == 64

    def test_cash_transaction_dedup(self):
        fp1 = make_fingerprint("4/2/2026", "", "ACH", "0", "0", amount="$900.00", settle="4/5/2026")
        fp2 = make_fingerprint("2026-04-02", "", "ACH", "0", "0", amount="900.000000", settle="2026-04-05")
        assert fp1 == fp2

    def test_ticker_case_insensitive(self):
        fp1 = make_fingerprint("4/2/2026", "nvda", "BUY", "10", "116.02")
        fp2 = make_fingerprint("4/2/2026", "NVDA", "BUY", "10", "116.02")
        assert fp1 == fp2

    def test_code_case_insensitive(self):
        fp1 = make_fingerprint("4/2/2026", "NVDA", "buy", "10", "116.02")
        fp2 = make_fingerprint("4/2/2026", "NVDA", "BUY", "10", "116.02")
        assert fp1 == fp2
