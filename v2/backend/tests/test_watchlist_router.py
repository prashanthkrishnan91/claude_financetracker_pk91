"""Watchlist evaluation — deterministic criteria checks; no stock picking."""

from __future__ import annotations

from app.routers.watchlist import _evaluate


class TestEvaluate:
    def test_price_below_met(self):
        assert _evaluate("price_below", 100.0, 90.0) is True

    def test_price_below_not_met(self):
        assert _evaluate("price_below", 100.0, 110.0) is False

    def test_price_above_met(self):
        assert _evaluate("price_above", 100.0, 110.0) is True

    def test_price_above_not_met(self):
        assert _evaluate("price_above", 100.0, 90.0) is False

    def test_exact_threshold_counts_as_met_both_directions(self):
        assert _evaluate("price_below", 100.0, 100.0) is True
        assert _evaluate("price_above", 100.0, 100.0) is True

    def test_missing_price_is_honestly_unknown(self):
        assert _evaluate("price_below", 100.0, None) is None

    def test_unknown_criteria_type_is_none(self):
        assert _evaluate("bogus", 100.0, 50.0) is None
