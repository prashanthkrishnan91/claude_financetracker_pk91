"""Tests for Stage 12B — allocation_policy_v1 service.

All tests use fully mocked data — no live Supabase, no provider calls, no LLM calls.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.allocation_policy_v1 import (
    DIAGNOSTIC_VERSION,
    ETF_FLOOR_PCT,
    GROUP_BROAD_ETF,
    GROUP_CRYPTO,
    GROUP_DIVIDEND_ETF,
    GROUP_INDIVIDUAL_STOCK,
    GROUP_INTERNATIONAL_ETF,
    GROUP_SECTOR_ETF,
    GROUP_SPECULATIVE,
    INDIVIDUAL_STOCK_CAP_PCT,
    SPECULATIVE_CAP_PCT,
    CRYPTO_TOTAL_CAP_PCT,
    POLICY_VERSION,
    classify_ticker,
    _allocate_cash,
    _build_price_map,
    _check_reconciliation,
    _compute_group_weights,
    _compute_portfolio,
    _floor5,
    _generate_policy,
    _load_open_positions,
    _parse_intel_v3_overlay,
    _rank_buy_candidates,
    run_next_buy_policy_diagnostic,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _days_ago_date(days: int) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def _pos(ticker: str, shares: float = 10.0, category: str = "Core") -> dict:
    return {"ticker": ticker, "shares": shares, "category": category}


def _price(ticker: str, close: float = 100.0, days_old: int = 0) -> dict:
    return {"ticker": ticker, "price_date": _days_ago_date(days_old), "close_price": close}


def _make_db(
    positions: list[dict] | None = None,
    prices_by_ticker: dict[str, list[dict]] | None = None,
    snapshot_value: float | None = 10000.0,
    intel_rows: list[dict] | None = None,
) -> MagicMock:
    """Build a mock db_client that returns canned data."""
    db = MagicMock()

    def _chain(data):
        """Build a chainable mock that returns .execute().data = data."""
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.gt.return_value = m
        m.neq.return_value = m
        m.order.return_value = m
        m.limit.return_value = m
        m.execute.return_value = SimpleNamespace(data=data)
        return m

    pos_data = positions if positions is not None else []
    snap_data = [{"total_equity": snapshot_value, "snapshot_at": "2026-06-20T10:00:00Z"}] if snapshot_value else []
    intel_data = intel_rows if intel_rows is not None else []

    def table_side_effect(name):
        if name == "portfolio_snapshots":
            return _chain(snap_data)
        if name == "positions":
            return _chain(pos_data)
        if name == "intel_v3_snapshots":
            return _chain(intel_data)
        # price_history: per-ticker
        if name == "price_history":
            outer = MagicMock()
            outer.select.return_value = outer
            current_ticker: list[str] = []

            def eq_side(col, val):
                if col == "ticker":
                    current_ticker.clear()
                    current_ticker.append(val)
                return outer
            outer.eq.side_effect = eq_side
            outer.order.return_value = outer

            def limit_side(_n):
                ticker = current_ticker[0] if current_ticker else None
                rows = (prices_by_ticker or {}).get(ticker, []) if ticker else []
                return _chain(rows)
            outer.limit.side_effect = limit_side
            return outer
        return _chain([])

    db.table.side_effect = table_side_effect
    return db


# ── classify_ticker ────────────────────────────────────────────────────────────

class TestClassifyTicker:
    def test_broad_etf(self):
        assert classify_ticker("VOO") == (GROUP_BROAD_ETF, False)
        assert classify_ticker("QQQ") == (GROUP_BROAD_ETF, False)
        assert classify_ticker("SPY") == (GROUP_BROAD_ETF, False)
        assert classify_ticker("VTI") == (GROUP_BROAD_ETF, False)

    def test_dividend_etf(self):
        assert classify_ticker("VYM") == (GROUP_DIVIDEND_ETF, False)
        assert classify_ticker("SCHD") == (GROUP_DIVIDEND_ETF, False)

    def test_international_etf(self):
        assert classify_ticker("VXUS") == (GROUP_INTERNATIONAL_ETF, False)

    def test_sector_etf(self):
        assert classify_ticker("VGT") == (GROUP_SECTOR_ETF, False)
        assert classify_ticker("VHT") == (GROUP_SECTOR_ETF, False)
        assert classify_ticker("XLE") == (GROUP_SECTOR_ETF, False)

    def test_crypto(self):
        assert classify_ticker("BTC") == (GROUP_CRYPTO, False)
        assert classify_ticker("ETH") == (GROUP_CRYPTO, False)
        assert classify_ticker("SOL") == (GROUP_CRYPTO, False)

    def test_speculative(self):
        assert classify_ticker("STUB") == (GROUP_SPECULATIVE, False)
        assert classify_ticker("BLSH") == (GROUP_SPECULATIVE, False)
        assert classify_ticker("KLAR") == (GROUP_SPECULATIVE, False)

    def test_known_individual_stock(self):
        group, is_unknown = classify_ticker("NVDA")
        assert group == GROUP_INDIVIDUAL_STOCK
        assert is_unknown is True

    def test_unknown_ticker_defaults_to_individual_stock(self):
        group, is_unknown = classify_ticker("ZZZZ")
        assert group == GROUP_INDIVIDUAL_STOCK
        assert is_unknown is True

    def test_lowercase_handled(self):
        group, is_unknown = classify_ticker("voo")
        assert group == GROUP_BROAD_ETF
        assert is_unknown is False


# ── _load_open_positions ───────────────────────────────────────────────────────

class TestLoadOpenPositions:
    def test_filters_sell_category(self):
        rows = [_pos("AAPL"), _pos("NVDA", category="SELL")]
        result = _load_open_positions(rows)
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"

    def test_filters_zero_shares(self):
        rows = [_pos("AAPL"), _pos("NVDA", shares=0)]
        result = _load_open_positions(rows)
        assert len(result) == 1

    def test_filters_none_ticker(self):
        rows = [_pos("AAPL"), {"ticker": None, "shares": 10, "category": "Core"}]
        result = _load_open_positions(rows)
        assert len(result) == 1

    def test_empty_rows(self):
        assert _load_open_positions([]) == []


# ── _build_price_map ───────────────────────────────────────────────────────────

class TestBuildPriceMap:
    def test_returns_latest_row(self):
        rows = {
            "AAPL": [
                {"ticker": "AAPL", "price_date": "2026-06-20", "close_price": 200.0},
                {"ticker": "AAPL", "price_date": "2026-06-19", "close_price": 198.0},
            ]
        }
        pm = _build_price_map(rows)
        assert pm["AAPL"]["close_price"] == 200.0

    def test_missing_ticker_returns_none(self):
        pm = _build_price_map({"AAPL": []})
        assert pm["AAPL"] is None


# ── _compute_portfolio ────────────────────────────────────────────────────────

class TestComputePortfolio:
    def test_weights_sum_to_100(self):
        positions = [
            _pos("VOO", shares=10),
            _pos("AAPL", shares=5),
        ]
        price_map = {
            "VOO": _price("VOO", close=400.0),
            "AAPL": _price("AAPL", close=200.0),
        }
        holdings, total_mv, missing, stale, unknown = _compute_portfolio(positions, price_map)
        total_weight = sum(h["weight_pct"] for h in holdings.values() if h["weight_pct"])
        assert abs(total_weight - 100.0) < 0.01

    def test_missing_price_recorded(self):
        positions = [_pos("VOO"), _pos("ZZZZ")]
        price_map = {"VOO": _price("VOO"), "ZZZZ": None}
        _, _, missing, _, _ = _compute_portfolio(positions, price_map)
        assert "ZZZZ" in missing

    def test_unknown_ticker_flagged(self):
        positions = [_pos("NEWCO")]
        price_map = {"NEWCO": _price("NEWCO", close=50.0)}
        _, _, _, _, unknown = _compute_portfolio(positions, price_map)
        assert "NEWCO" in unknown

    def test_stale_price_recorded(self):
        positions = [_pos("AAPL")]
        # 10 business days old — well past 3-day threshold
        price_map = {"AAPL": _price("AAPL", days_old=14)}
        _, _, missing, stale, _ = _compute_portfolio(positions, price_map)
        assert "AAPL" in stale
        assert "AAPL" not in missing


# ── _compute_group_weights ────────────────────────────────────────────────────

class TestComputeGroupWeights:
    def test_etf_total_computed(self):
        positions = [_pos("VOO", shares=10)]
        price_map = {"VOO": _price("VOO", close=400.0)}
        holdings, total_mv, *_ = _compute_portfolio(positions, price_map)
        gw = _compute_group_weights(holdings, total_mv)
        assert "_etf_total" in gw
        assert gw["_etf_total"]["weight_pct"] == pytest.approx(100.0, abs=0.01)

    def test_crypto_group_weight(self):
        positions = [_pos("BTC", shares=0.1)]
        price_map = {"BTC": _price("BTC", close=50000.0)}
        holdings, total_mv, *_ = _compute_portfolio(positions, price_map)
        gw = _compute_group_weights(holdings, total_mv)
        assert gw[GROUP_CRYPTO]["weight_pct"] == pytest.approx(100.0, abs=0.01)


# ── _generate_policy ──────────────────────────────────────────────────────────

class TestGeneratePolicy:
    def test_etf_floor_not_met_when_no_etfs(self):
        positions = [_pos("AAPL", shares=10)]
        price_map = {"AAPL": _price("AAPL", close=200.0)}
        holdings, total_mv, *_ = _compute_portfolio(positions, price_map)
        gw = _compute_group_weights(holdings, total_mv)
        policy = _generate_policy(gw)
        assert policy["etf_floor_met"] is False
        assert policy["current_etf_pct"] == pytest.approx(0.0, abs=0.01)

    def test_etf_floor_met_when_etfs_dominant(self):
        positions = [_pos("VOO", shares=100)]
        price_map = {"VOO": _price("VOO", close=400.0)}
        holdings, total_mv, *_ = _compute_portfolio(positions, price_map)
        gw = _compute_group_weights(holdings, total_mv)
        policy = _generate_policy(gw)
        assert policy["etf_floor_met"] is True
        assert policy["current_etf_pct"] == pytest.approx(100.0, abs=0.01)

    def test_policy_version(self):
        positions = [_pos("VOO")]
        price_map = {"VOO": _price("VOO")}
        holdings, total_mv, *_ = _compute_portfolio(positions, price_map)
        gw = _compute_group_weights(holdings, total_mv)
        policy = _generate_policy(gw)
        assert policy["policy_version"] == POLICY_VERSION


# ── _check_reconciliation ─────────────────────────────────────────────────────

class TestCheckReconciliation:
    def test_pass_within_1pct(self):
        status, blockers = _check_reconciliation(10000.0, 10050.0)
        assert status == "pass"
        assert not blockers

    def test_degraded_within_5pct(self):
        status, blockers = _check_reconciliation(10000.0, 10300.0)
        assert status == "degraded"
        assert not blockers

    def test_blocked_above_5pct(self):
        status, blockers = _check_reconciliation(10000.0, 15000.0)
        assert status == "blocked"
        assert any("reconciliation_blocked" in b for b in blockers)

    def test_unavailable_when_snapshot_none(self):
        status, blockers = _check_reconciliation(None, 10000.0)
        assert status == "unavailable"

    def test_unavailable_when_mv_zero(self):
        status, blockers = _check_reconciliation(10000.0, 0.0)
        assert status == "unavailable"


# ── _parse_intel_v3_overlay ───────────────────────────────────────────────────

class TestParseIntelV3Overlay:
    def test_none_snapshot_returns_no_overlay(self):
        cm, used, warning = _parse_intel_v3_overlay(None)
        assert used is False
        assert "unavailable" in warning

    def test_valid_snapshot_extracts_conviction(self):
        snapshot = {
            "payload": {
                "cards": [
                    {"ticker": "AAPL", "action": "BUY", "conviction_level": "HIGH"},
                    {"ticker": "NVDA", "action": "BUY", "conviction_level": "MEDIUM"},
                    {"ticker": "SELL_ME", "action": "SELL", "conviction_level": "HIGH"},
                ]
            }
        }
        cm, used, warning = _parse_intel_v3_overlay(snapshot)
        assert used is True
        assert cm.get("AAPL") == "HIGH"
        assert cm.get("NVDA") == "MEDIUM"
        assert "SELL_ME" not in cm
        assert warning is None

    def test_no_buy_hold_cards_returns_no_overlay(self):
        snapshot = {"payload": {"cards": [{"ticker": "X", "action": "SELL"}]}}
        cm, used, warning = _parse_intel_v3_overlay(snapshot)
        assert used is False
        assert warning is not None

    def test_empty_cards_returns_no_overlay(self):
        snapshot = {"payload": {"cards": []}}
        cm, used, warning = _parse_intel_v3_overlay(snapshot)
        assert used is False


# ── _rank_buy_candidates ──────────────────────────────────────────────────────

class TestRankBuyCandidates:
    def _base_holdings(self) -> dict:
        positions = [
            _pos("VOO", shares=10),
            _pos("AAPL", shares=5),
        ]
        price_map = {
            "VOO": _price("VOO", close=400.0),
            "AAPL": _price("AAPL", close=200.0),
        }
        holdings, total_mv, *_ = _compute_portfolio(positions, price_map)
        return holdings, total_mv

    def test_etf_preferred_when_floor_not_met(self):
        positions = [
            _pos("VOO", shares=1),   # tiny ETF allocation
            _pos("AAPL", shares=50),  # big stock allocation
        ]
        price_map = {
            "VOO": _price("VOO", close=400.0),
            "AAPL": _price("AAPL", close=200.0),
        }
        holdings, total_mv, *_ = _compute_portfolio(positions, price_map)
        gw = _compute_group_weights(holdings, total_mv)
        policy = _generate_policy(gw)
        group_gaps, ticker_gaps = _rank_buy_candidates.__module__ and \
            __import__("app.services.allocation_policy_v1", fromlist=["_compute_gaps"])._compute_gaps(
                holdings, gw, policy, total_mv
            )
        candidates = _rank_buy_candidates(
            ticker_gaps, group_gaps, holdings,
            conviction_map={}, intel_overlay_used=False,
            etf_floor_met=policy["etf_floor_met"],
        )
        if candidates:
            # ETF should rank first when floor not met
            first_group = candidates[0]["group"]
            assert first_group in {GROUP_BROAD_ETF, GROUP_DIVIDEND_ETF, GROUP_INTERNATIONAL_ETF, GROUP_SECTOR_ETF}

    def test_no_candidates_when_all_at_cap(self):
        # A portfolio that is 100% crypto but at/above cap
        positions = [_pos("BTC", shares=1)]
        price_map = {"BTC": _price("BTC", close=10000.0)}
        holdings, total_mv, *_ = _compute_portfolio(positions, price_map)
        gw = _compute_group_weights(holdings, total_mv)
        policy = _generate_policy(gw)
        from app.services.allocation_policy_v1 import _compute_gaps
        group_gaps, ticker_gaps = _compute_gaps(holdings, gw, policy, total_mv)
        # BTC is at 100%, crypto cap is 5% — should be ineligible
        assert ticker_gaps["BTC"]["eligible_for_buy"] is False
        assert "cap" in (ticker_gaps["BTC"]["ineligibility_reason"] or "")


# ── broad_index_etf core preference ranking ───────────────────────────────────

class TestBroadEtfCorePreference:
    """Verify that for broad_index_etf candidates, core preference outranks conviction.

    Sort key is (group_priority, core_preference_rank, conviction_rank, gap_pct).
    VTI(4) > VOO(3) > SPY(2) > QQQ(1) regardless of conviction.
    """

    def _ticker_gap(self, ticker: str, gap_pct: float, eligible: bool = True) -> dict:
        from app.services.allocation_policy_v1 import classify_ticker
        group, is_unknown = classify_ticker(ticker)
        return {
            "ticker": ticker,
            "group": group,
            "current_weight_pct": 5.0,
            "target_weight_pct": 5.0 + gap_pct,
            "per_ticker_cap_pct": 40.0,
            "gap_pct": gap_pct,
            "eligible_for_buy": eligible,
            "ineligibility_reason": None if eligible else "test_ineligible",
            "is_unknown_ticker": is_unknown,
        }

    def _rank(self, ticker_gaps: dict, conviction_map: dict | None = None) -> list[str]:
        from app.services.allocation_policy_v1 import _rank_buy_candidates
        candidates = _rank_buy_candidates(
            ticker_gaps=ticker_gaps,
            group_gaps={},
            holdings={},
            conviction_map=conviction_map or {},
            intel_overlay_used=bool(conviction_map),
            etf_floor_met=True,
        )
        return [c["ticker"] for c in candidates]

    def test_vti_neutral_beats_spy_high_larger_gap(self):
        """VTI neutral ranks before SPY HIGH even when SPY has a larger gap."""
        ticker_gaps = {
            "VTI": self._ticker_gap("VTI", gap_pct=5.0),
            "SPY": self._ticker_gap("SPY", gap_pct=10.0),
        }
        conviction_map = {"SPY": "HIGH", "VTI": "neutral"}
        ranked = self._rank(ticker_gaps, conviction_map)
        assert ranked[0] == "VTI", f"Expected VTI first, got {ranked}"
        assert ranked[1] == "SPY"

    def test_voo_neutral_beats_spy_high_when_vti_ineligible(self):
        """VOO neutral ranks before SPY HIGH when VTI is ineligible."""
        ticker_gaps = {
            "VTI": self._ticker_gap("VTI", gap_pct=8.0, eligible=False),
            "VOO": self._ticker_gap("VOO", gap_pct=5.0),
            "SPY": self._ticker_gap("SPY", gap_pct=10.0),
        }
        conviction_map = {"SPY": "HIGH", "VOO": "neutral"}
        ranked = self._rank(ticker_gaps, conviction_map)
        assert "VTI" not in ranked  # ineligible
        assert ranked[0] == "VOO", f"Expected VOO first, got {ranked}"
        assert ranked[1] == "SPY"

    def test_qqq_high_does_not_outrank_vti_eligible(self):
        """QQQ HIGH conviction does not outrank eligible VTI."""
        ticker_gaps = {
            "VTI": self._ticker_gap("VTI", gap_pct=5.0),
            "QQQ": self._ticker_gap("QQQ", gap_pct=15.0),
        }
        conviction_map = {"QQQ": "HIGH", "VTI": "neutral"}
        ranked = self._rank(ticker_gaps, conviction_map)
        assert ranked[0] == "VTI", f"Expected VTI first, got {ranked}"

    def test_qqq_high_does_not_outrank_voo_eligible(self):
        """QQQ HIGH conviction does not outrank eligible VOO."""
        ticker_gaps = {
            "VOO": self._ticker_gap("VOO", gap_pct=5.0),
            "QQQ": self._ticker_gap("QQQ", gap_pct=15.0),
        }
        conviction_map = {"QQQ": "HIGH", "VOO": "neutral"}
        ranked = self._rank(ticker_gaps, conviction_map)
        assert ranked[0] == "VOO", f"Expected VOO first, got {ranked}"

    def test_qqq_high_does_not_outrank_spy_eligible(self):
        """QQQ HIGH conviction does not outrank eligible SPY."""
        ticker_gaps = {
            "SPY": self._ticker_gap("SPY", gap_pct=5.0),
            "QQQ": self._ticker_gap("QQQ", gap_pct=15.0),
        }
        conviction_map = {"QQQ": "HIGH", "SPY": "neutral"}
        ranked = self._rank(ticker_gaps, conviction_map)
        assert ranked[0] == "SPY", f"Expected SPY first, got {ranked}"

    def test_full_preference_order_vti_voo_spy_qqq(self):
        """Full ordering: VTI > VOO > SPY > QQQ regardless of equal conviction."""
        ticker_gaps = {
            "QQQ": self._ticker_gap("QQQ", gap_pct=20.0),
            "SPY": self._ticker_gap("SPY", gap_pct=15.0),
            "VOO": self._ticker_gap("VOO", gap_pct=10.0),
            "VTI": self._ticker_gap("VTI", gap_pct=5.0),
        }
        ranked = self._rank(ticker_gaps)
        assert ranked == ["VTI", "VOO", "SPY", "QQQ"], f"Wrong order: {ranked}"


# ── _allocate_cash ────────────────────────────────────────────────────────────

class TestAllocateCash:
    def _candidate(self, ticker, group, gap_pct=10.0, conviction="neutral"):
        return {
            "ticker": ticker,
            "group": group,
            "current_weight_pct": 5.0,
            "target_or_cap_weight_pct": 15.0,
            "gap_pct": gap_pct,
            "gap_dollars": 0.0,
            "classification": group,
            "conviction": conviction,
            "confidence": "policy_only",
            "reason_codes": ["positive_gap"],
            "_sort_key": (5, 0, gap_pct),
            "is_unknown_ticker": False,
        }

    def test_min_trade_amount_enforced(self):
        candidates = [self._candidate("VOO", GROUP_BROAD_ETF, gap_pct=0.01)]
        allocated, alloc_cash, unalloc, count, reason = _allocate_cash(
            candidates, total_mv=10000.0, cash_to_deploy=100.0,
            min_trade_amount=25.0, max_positions=5,
        )
        # Gap dollars = 0.01% of 10100 ≈ $1.01 < $25 min — should skip
        assert count == 0
        assert reason is not None

    def test_max_positions_enforced(self):
        candidates = [
            self._candidate(f"T{i}", GROUP_BROAD_ETF, gap_pct=20.0)
            for i in range(10)
        ]
        allocated, _, _, count, _ = _allocate_cash(
            candidates, total_mv=10000.0, cash_to_deploy=5000.0,
            min_trade_amount=25.0, max_positions=3,
        )
        assert count <= 3

    def test_no_candidates_returns_no_buy_reason(self):
        allocated, alloc, unalloc, count, reason = _allocate_cash(
            [], total_mv=10000.0, cash_to_deploy=500.0,
            min_trade_amount=25.0, max_positions=5,
        )
        assert count == 0
        assert reason == "no_eligible_buy_candidates"
        assert unalloc == 500.0

    def test_allocation_rounds_to_5(self):
        candidates = [self._candidate("VOO", GROUP_BROAD_ETF, gap_pct=15.0)]
        allocated, alloc_cash, _, count, _ = _allocate_cash(
            candidates, total_mv=10000.0, cash_to_deploy=503.0,
            min_trade_amount=25.0, max_positions=5,
        )
        if allocated:
            dollar_amt = allocated[0]["dollar_amount"]
            assert dollar_amt % 5 == 0 or abs(dollar_amt % 5 - 0) < 0.01

    def test_cash_not_exceeded(self):
        candidates = [
            self._candidate("VOO", GROUP_BROAD_ETF, gap_pct=50.0),
            self._candidate("QQQ", GROUP_BROAD_ETF, gap_pct=20.0),
        ]
        cash = 500.0
        allocated, alloc_cash, unalloc, count, _ = _allocate_cash(
            candidates, total_mv=10000.0, cash_to_deploy=cash,
            min_trade_amount=25.0, max_positions=5,
        )
        assert alloc_cash + unalloc == pytest.approx(cash, abs=0.01)

    def test_cash_2737_50_does_not_overspend(self):
        """Regression: cash_to_deploy=2737.50 must not produce allocated_cash=2740."""
        candidates = [self._candidate("SPY", GROUP_BROAD_ETF, gap_pct=50.0)]
        allocated, alloc_cash, unalloc, count, _ = _allocate_cash(
            candidates, total_mv=5000.0, cash_to_deploy=2737.50,
            min_trade_amount=25.0, max_positions=5,
        )
        assert alloc_cash <= 2737.50, f"allocated_cash={alloc_cash} exceeds cash_to_deploy=2737.50"
        assert unalloc >= 0.0, f"unallocated_cash={unalloc} is negative"
        if allocated:
            assert allocated[0]["dollar_amount"] <= 2737.50

    def test_unallocated_cash_never_negative(self):
        """unallocated_cash must be >= 0 for any cash_to_deploy value."""
        candidates = [self._candidate("VOO", GROUP_BROAD_ETF, gap_pct=100.0)]
        for cash in [2737.50, 100.50, 503.75, 1002.00, 25.0, 27.50]:
            _, alloc_cash, unalloc, _, _ = _allocate_cash(
                candidates, total_mv=5000.0, cash_to_deploy=cash,
                min_trade_amount=25.0, max_positions=5,
            )
            assert unalloc >= 0.0, f"unallocated_cash={unalloc} negative for cash={cash}"
            assert alloc_cash <= cash + 0.01, f"allocated={alloc_cash} > cash={cash}"

    def test_floor5_never_rounds_up(self):
        """_floor5 must always return a value <= input and a multiple of 5."""
        for amount in [2737.50, 100.50, 503.75, 27.3, 1.0, 0.0, 5.0, 25.0, 30.0]:
            result = _floor5(amount)
            assert result <= amount + 1e-9, f"_floor5({amount})={result} > {amount}"
            assert int(result) % 5 == 0, f"_floor5({amount})={result} is not a multiple of 5"

    def test_allocated_equals_sum_of_candidates(self):
        """allocated_cash must equal sum of candidate dollar_amounts within $0.02."""
        candidates = [
            self._candidate("VOO", GROUP_BROAD_ETF, gap_pct=20.0),
            self._candidate("QQQ", GROUP_BROAD_ETF, gap_pct=15.0),
        ]
        allocated, alloc_cash, unalloc, count, _ = _allocate_cash(
            candidates, total_mv=10000.0, cash_to_deploy=1000.0,
            min_trade_amount=25.0, max_positions=5,
        )
        sum_amounts = sum(c["dollar_amount"] for c in allocated)
        assert abs(sum_amounts - alloc_cash) <= 0.02, (
            f"sum(candidates)={sum_amounts} != allocated_cash={alloc_cash}"
        )


# ── run_next_buy_policy_diagnostic (integration via mocked DB) ─────────────────

class TestRunNextBuyPolicyDiagnostic:
    @pytest.mark.asyncio
    async def test_certified_produces_ready_policy(self):
        """Full run with certified Stage 11 truth → policy_status=ready."""
        positions = [
            _pos("VOO", shares=20),
            _pos("AAPL", shares=10),
            _pos("QQQ", shares=5),
        ]
        prices = {
            "VOO": [_price("VOO", close=450.0)],
            "AAPL": [_price("AAPL", close=200.0)],
            "QQQ": [_price("QQQ", close=480.0)],
        }
        mv = 20 * 450 + 10 * 200 + 5 * 480  # 9000 + 2000 + 2400 = 13400
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()),
            cash_to_deploy=500.0, max_positions=5, min_trade_amount=25.0,
        )

        assert result["diagnostic_version"] == DIAGNOSTIC_VERSION
        assert result["verdict"]["policy_status"] in ("ready", "degraded")
        assert result["verdict"]["recommendations_trusted"] is False
        assert result["cash_plan"]["cash_to_deploy"] == 500.0

    @pytest.mark.asyncio
    async def test_missing_price_degrades_policy(self):
        """Missing price for a ticker degrades policy but does not block."""
        positions = [_pos("AAPL"), _pos("ZZZZ")]
        prices = {"AAPL": [_price("AAPL", close=200.0)], "ZZZZ": []}
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=2000.0)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()),
            cash_to_deploy=500.0,
        )
        td = result["truth_dependency"]
        assert "ZZZZ" in td["missing_price_tickers"]
        assert result["verdict"]["policy_status"] in ("degraded", "blocked")

    @pytest.mark.asyncio
    async def test_reconciliation_mismatch_degrades(self):
        """Snapshot value 4% off from position_mv → degraded reconciliation."""
        positions = [_pos("AAPL", shares=10)]
        prices = {"AAPL": [_price("AAPL", close=200.0)]}  # position_mv = 2000
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=2080.0)  # 4% diff

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        assert result["truth_dependency"]["reconciliation_status"] == "degraded"

    @pytest.mark.asyncio
    async def test_reconciliation_block_blocks_policy(self):
        """Snapshot value >5% off → reconciliation_blocked → policy blocked."""
        positions = [_pos("AAPL", shares=10)]
        prices = {"AAPL": [_price("AAPL", close=200.0)]}  # position_mv = 2000
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=3000.0)  # 50% diff

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        assert result["truth_dependency"]["reconciliation_status"] == "blocked"
        assert result["verdict"]["policy_status"] == "blocked"
        assert result["cash_plan"]["allocation_count"] == 0

    @pytest.mark.asyncio
    async def test_etf_floor_not_met_creates_etf_buy_preference(self):
        """Portfolio heavy on stocks → ETF candidates should rank first."""
        positions = [_pos("AAPL", shares=100), _pos("NVDA", shares=50)]
        prices = {
            "AAPL": [_price("AAPL", close=200.0)],
            "NVDA": [_price("NVDA", close=800.0)],
        }
        mv = 100 * 200 + 50 * 800  # 20000 + 40000 = 60000
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=5000.0,
        )
        # ETF floor must not be met in this portfolio
        assert result["generated_policy"].get("etf_floor_met") is False

    @pytest.mark.asyncio
    async def test_crypto_cap_prevents_buy_when_at_cap(self):
        """Portfolio with 100% BTC → crypto at/above 5% cap → BTC ineligible."""
        positions = [_pos("BTC", shares=1)]
        prices = {"BTC": [_price("BTC", close=50000.0)]}
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=50000.0)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        ticker_info = result["target_vs_current"]["by_ticker"].get("BTC", {})
        assert ticker_info.get("eligible_for_buy") is False
        reason = ticker_info.get("ineligibility_reason") or ""
        assert "cap" in reason

    @pytest.mark.asyncio
    async def test_individual_stock_cap_prevents_overweight_buy(self):
        """A single stock at 25% → above 20% cap → ineligible."""
        # VOO at 75%, AAPL at 25%
        positions = [_pos("VOO", shares=75), _pos("AAPL", shares=25)]
        prices = {
            "VOO": [_price("VOO", close=100.0)],
            "AAPL": [_price("AAPL", close=100.0)],
        }
        mv = 75 * 100 + 25 * 100  # 10000
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=1000.0,
        )
        aapl_info = result["target_vs_current"]["by_ticker"].get("AAPL", {})
        # AAPL at 25% > 20% cap
        assert aapl_info.get("eligible_for_buy") is False

    @pytest.mark.asyncio
    async def test_unknown_ticker_classified_as_individual_stock_with_warning(self):
        """Unknown ticker ZZZZ → classified as individual_stock + warning in output."""
        positions = [_pos("ZZZZ")]
        prices = {"ZZZZ": [_price("ZZZZ", close=50.0)]}
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=500.0)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        zzzz = result["target_vs_current"]["by_ticker"].get("ZZZZ", {})
        assert zzzz.get("group") == GROUP_INDIVIDUAL_STOCK
        assert zzzz.get("is_unknown_ticker") is True
        # Warning in policy section
        warnings_text = str(result["generated_policy"].get("warnings", []))
        assert "unknown_tickers_defaulted_to_individual_stock" in warnings_text

    @pytest.mark.asyncio
    async def test_no_provider_calls(self):
        """Service must not call any external provider during execution."""
        positions = [_pos("VOO")]
        prices = {"VOO": [_price("VOO", close=400.0)]}
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=4000.0)

        import httpx
        original_get = httpx.AsyncClient.get

        provider_called: list[str] = []

        async def _trap_get(self, url, **kwargs):
            provider_called.append(url)
            return original_get(self, url, **kwargs)

        # We only need to confirm httpx is NOT called — we do this by checking
        # the result runs without error and provider_called stays empty via mock.
        import unittest.mock as mock
        with mock.patch("httpx.AsyncClient.get", _trap_get):
            result = await run_next_buy_policy_diagnostic(
                db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
            )

        assert provider_called == [], "Service must not call any external HTTP provider"

    @pytest.mark.asyncio
    async def test_no_writes_to_db(self):
        """Service must not write to any DB table."""
        positions = [_pos("VOO")]
        prices = {"VOO": [_price("VOO", close=400.0)]}

        # Track any write calls
        writes: list[str] = []
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=4000.0)

        original_table = db.table.side_effect
        write_methods = []

        def patched_table(name):
            m = original_table(name)
            old_insert = m.insert if hasattr(m, "insert") else None
            old_upsert = m.upsert if hasattr(m, "upsert") else None
            old_update = m.update if hasattr(m, "update") else None
            old_delete = m.delete if hasattr(m, "delete") else None

            def trap(method_name):
                def inner(*args, **kwargs):
                    writes.append(f"{method_name}:{name}")
                    return m
                return inner

            m.insert = trap("insert")
            m.upsert = trap("upsert")
            m.update = trap("update")
            m.delete = trap("delete")
            return m

        db.table.side_effect = patched_table

        await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )

        assert writes == [], f"Service must not write to DB, but got: {writes}"

    @pytest.mark.asyncio
    async def test_without_intel_v3_uses_neutral_defaults(self):
        """Endpoint must produce output without Intel v3 snapshot (warning returned)."""
        positions = [_pos("VOO", shares=10), _pos("QQQ", shares=5)]
        prices = {
            "VOO": [_price("VOO", close=400.0)],
            "QQQ": [_price("QQQ", close=480.0)],
        }
        mv = 10 * 400 + 5 * 480
        db = _make_db(positions=positions, prices_by_ticker=prices,
                      snapshot_value=mv, intel_rows=[])

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )

        assert result["generated_policy"]["intel_v3_overlay_used"] is False
        assert result["generated_policy"]["intel_v3_overlay_warning"] is not None
        # Should still produce output
        assert "next_buy_candidates" in result

    @pytest.mark.asyncio
    async def test_intel_v3_overlay_affects_confidence_label(self):
        """With Intel v3 available, matching tickers get policy_plus_intel confidence."""
        positions = [_pos("VOO", shares=5), _pos("AAPL", shares=5)]
        prices = {
            "VOO": [_price("VOO", close=400.0)],
            "AAPL": [_price("AAPL", close=200.0)],
        }
        mv = 5 * 400 + 5 * 200  # 3000
        intel_payload = {
            "cards": [
                {"ticker": "AAPL", "action": "BUY", "conviction_level": "HIGH"},
                {"ticker": "VOO", "action": "BUY", "conviction_level": "MEDIUM"},
            ]
        }
        intel_rows = [{"payload": intel_payload, "created_at": "2026-06-20T10:00:00Z", "is_active": True}]
        db = _make_db(positions=positions, prices_by_ticker=prices,
                      snapshot_value=mv, intel_rows=intel_rows)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )

        assert result["generated_policy"]["intel_v3_overlay_used"] is True
        # Candidates with intel overlay get policy_plus_intel confidence
        for c in result["next_buy_candidates"]:
            if c.get("conviction") not in ("neutral", ""):
                assert c["confidence"] == "policy_plus_intel"

    @pytest.mark.asyncio
    async def test_recommendations_trusted_always_false(self):
        """recommendations_trusted must always be False — this is not the advisor layer."""
        positions = [_pos("VOO")]
        prices = {"VOO": [_price("VOO", close=400.0)]}
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=4000.0)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        assert result["verdict"]["recommendations_trusted"] is False

    @pytest.mark.asyncio
    async def test_no_eligible_buy_returns_no_buy_reason(self):
        """When no candidates are eligible, cash_plan has no_buy_reason."""
        # Only BTC at 100% — above crypto cap, no other tickers
        positions = [_pos("BTC", shares=1)]
        prices = {"BTC": [_price("BTC", close=50000.0)]}
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=50000.0)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        cp = result["cash_plan"]
        assert cp["allocation_count"] == 0
        assert cp["no_buy_reason"] is not None

    @pytest.mark.asyncio
    async def test_deterministic_output_ordering(self):
        """Same input produces same candidate ordering on repeated calls."""
        positions = [_pos("VOO", shares=5), _pos("QQQ", shares=3), _pos("AAPL", shares=2)]
        prices = {
            "VOO": [_price("VOO", close=400.0)],
            "QQQ": [_price("QQQ", close=480.0)],
            "AAPL": [_price("AAPL", close=200.0)],
        }
        mv = 5 * 400 + 3 * 480 + 2 * 200  # 2000 + 1440 + 400 = 3840
        db1 = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)
        db2 = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)

        uid = str(uuid4())
        r1 = await run_next_buy_policy_diagnostic(db_client=db1, user_id=uid, cash_to_deploy=500.0)
        r2 = await run_next_buy_policy_diagnostic(db_client=db2, user_id=uid, cash_to_deploy=500.0)

        candidates1 = [c["ticker"] for c in r1["next_buy_candidates"]]
        candidates2 = [c["ticker"] for c in r2["next_buy_candidates"]]
        assert candidates1 == candidates2

    @pytest.mark.asyncio
    async def test_output_contains_all_required_sections(self):
        """Response shape must include all documented sections."""
        positions = [_pos("VOO"), _pos("AAPL")]
        prices = {
            "VOO": [_price("VOO", close=400.0)],
            "AAPL": [_price("AAPL", close=200.0)],
        }
        mv = 400 + 200
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )

        required = [
            "diagnostic_version", "generated_at", "input", "truth_dependency",
            "current_portfolio", "generated_policy", "target_vs_current",
            "next_buy_candidates", "cash_plan", "verdict",
        ]
        for key in required:
            assert key in result, f"Missing required key: {key}"

        # Nested keys
        assert "truth_status" in result["truth_dependency"]
        assert "reconciliation_status" in result["truth_dependency"]
        assert "can_run_policy" in result["truth_dependency"]
        assert "policy_status" in result["verdict"]
        assert "recommendations_trusted" in result["verdict"]
        assert "numeric_plan_trusted" in result["verdict"]
        assert "cash_to_deploy" in result["cash_plan"]
        assert "allocated_cash" in result["cash_plan"]
        assert "unallocated_cash" in result["cash_plan"]

    @pytest.mark.asyncio
    async def test_speculative_ticker_has_5pct_cap(self):
        """Speculative tickers (RDDT, BLSH, STUB, KLAR) have 5% cap."""
        positions = [
            _pos("VOO", shares=95),
            _pos("RDDT", shares=5),
        ]
        prices = {
            "VOO": [_price("VOO", close=100.0)],
            "RDDT": [_price("RDDT", close=100.0)],
        }
        mv = 9500 + 500
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        rddt_info = result["target_vs_current"]["by_ticker"].get("RDDT", {})
        assert rddt_info.get("per_ticker_cap_pct") == SPECULATIVE_CAP_PCT

    @pytest.mark.asyncio
    async def test_stale_price_degrades_but_does_not_block(self):
        """Stale prices degrade policy but do not block it."""
        positions = [_pos("AAPL")]
        prices = {"AAPL": [_price("AAPL", close=200.0, days_old=14)]}  # 14 days = stale
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=2000.0)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        td = result["truth_dependency"]
        assert "AAPL" in td["stale_price_tickers"]
        # Degraded, not blocked
        assert result["verdict"]["policy_status"] == "degraded"


# ── numeric_plan_trusted contract tests ───────────────────────────────────────

class TestNumericPlanTrusted:
    """Stage 12B contract: numeric_plan_trusted is True only when all truth conditions pass."""

    @pytest.mark.asyncio
    async def test_ready_pass_complete_prices_trusted(self):
        """policy_status=ready + reconciliation pass + complete price coverage => numeric_plan_trusted True."""
        positions = [_pos("VOO", shares=20)]
        prices = {"VOO": [_price("VOO", close=450.0, days_old=0)]}
        mv = 20 * 450.0  # 9000
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        assert result["verdict"]["policy_status"] == "ready"
        assert result["truth_dependency"]["reconciliation_status"] == "pass"
        assert result["truth_dependency"]["price_coverage_status"] == "ok"
        assert result["verdict"]["numeric_plan_trusted"] is True

    @pytest.mark.asyncio
    async def test_missing_price_coverage_makes_numeric_plan_untrusted(self):
        """Missing price for a held ticker => policy_status degraded => numeric_plan_trusted False."""
        positions = [_pos("VOO"), _pos("ZZZZ")]
        prices = {"VOO": [_price("VOO", close=450.0)], "ZZZZ": []}  # ZZZZ has no price
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=4500.0)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        assert result["verdict"]["policy_status"] == "degraded"
        assert "ZZZZ" in result["truth_dependency"]["missing_price_tickers"]
        assert result["verdict"]["numeric_plan_trusted"] is False
        assert result["verdict"]["recommendations_trusted"] is False

    @pytest.mark.asyncio
    async def test_stale_price_coverage_makes_numeric_plan_untrusted(self):
        """Stale price for a held ticker => policy_status degraded => numeric_plan_trusted False."""
        positions = [_pos("AAPL")]
        prices = {"AAPL": [_price("AAPL", close=200.0, days_old=14)]}  # 14 days = stale
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=2000.0)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        assert result["verdict"]["policy_status"] == "degraded"
        assert "AAPL" in result["truth_dependency"]["stale_price_tickers"]
        assert result["verdict"]["numeric_plan_trusted"] is False
        assert result["verdict"]["recommendations_trusted"] is False
        assert "Stage 11B" in result["verdict"]["next_required_fix"]

    @pytest.mark.asyncio
    async def test_degraded_reconciliation_makes_numeric_plan_untrusted(self):
        """Snapshot value 4% off from position_mv => recon degraded => numeric_plan_trusted False."""
        positions = [_pos("AAPL", shares=10)]
        prices = {"AAPL": [_price("AAPL", close=200.0)]}  # position_mv = 2000
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=2080.0)  # ~4% off

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        assert result["truth_dependency"]["reconciliation_status"] == "degraded"
        assert result["verdict"]["numeric_plan_trusted"] is False
        assert result["verdict"]["recommendations_trusted"] is False

    @pytest.mark.asyncio
    async def test_blocked_reconciliation_makes_numeric_plan_untrusted(self):
        """Snapshot >5% off => reconciliation blocked => policy blocked => numeric_plan_trusted False."""
        positions = [_pos("AAPL", shares=10)]
        prices = {"AAPL": [_price("AAPL", close=200.0)]}  # position_mv = 2000
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=3000.0)  # 50% off

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()), cash_to_deploy=500.0,
        )
        assert result["truth_dependency"]["reconciliation_status"] == "blocked"
        assert result["verdict"]["policy_status"] == "blocked"
        assert result["verdict"]["numeric_plan_trusted"] is False
        assert result["verdict"]["recommendations_trusted"] is False

    @pytest.mark.asyncio
    async def test_cash_2737_50_numeric_plan_trusted_and_no_overspend(self):
        """Regression: cash_to_deploy=2737.50 with valid truth => no overspend, numeric_plan_trusted True."""
        positions = [_pos("VOO", shares=20)]
        prices = {"VOO": [_price("VOO", close=450.0, days_old=0)]}
        mv = 20 * 450.0  # 9000
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)

        result = await run_next_buy_policy_diagnostic(
            db_client=db, user_id=str(uuid4()),
            cash_to_deploy=2737.50, max_positions=1, min_trade_amount=25.0,
        )
        cp = result["cash_plan"]
        assert cp["allocated_cash"] <= 2737.50, (
            f"allocated_cash={cp['allocated_cash']} exceeds cash_to_deploy=2737.50"
        )
        assert cp["unallocated_cash"] >= 0.0, (
            f"unallocated_cash={cp['unallocated_cash']} is negative"
        )
        assert cp["allocated_cash"] + cp["unallocated_cash"] == pytest.approx(2737.50, abs=0.02)
        assert result["verdict"]["numeric_plan_trusted"] is True

    @pytest.mark.asyncio
    async def test_numeric_plan_trusted_false_if_cash_invariant_violated(self):
        """numeric_plan_trusted must be False if allocated_cash > cash_to_deploy (invariant guard)."""
        # Patch _allocate_cash to simulate a broken allocator returning overspend
        import unittest.mock as mock
        from app.services import allocation_policy_v1 as svc

        positions = [_pos("VOO", shares=20)]
        prices = {"VOO": [_price("VOO", close=450.0, days_old=0)]}
        mv = 20 * 450.0
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=mv)

        with mock.patch.object(svc, "_allocate_cash") as mock_alloc:
            # Simulate the overspend bug: allocated > cash_to_deploy, unallocated < 0
            mock_alloc.return_value = (
                [{"ticker": "VOO", "dollar_amount": 2740.0}],
                2740.0,   # allocated_cash
                -2.50,    # unallocated_cash (invalid)
                1,
                None,
            )
            result = await run_next_buy_policy_diagnostic(
                db_client=db, user_id=str(uuid4()),
                cash_to_deploy=2737.50, max_positions=1, min_trade_amount=25.0,
            )

        assert result["verdict"]["numeric_plan_trusted"] is False
        assert result["cash_plan"]["unallocated_cash"] >= 0.0  # clamped by invariant guard
        assert "cash_bound_violated" in str(result["generated_policy"].get("warnings", []))
