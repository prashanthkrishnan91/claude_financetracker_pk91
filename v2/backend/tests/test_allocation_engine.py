"""Tests for the Portfolio Allocation Engine — scoring, constraints,
rounding, exclusions, and end-to-end acceptance criteria."""

from __future__ import annotations

import pytest

from app.services.allocation_engine import (
    CONVICTION_WEIGHTS,
    MAX_ETF_WEIGHT,
    MAX_SAME_THEME_WEIGHT,
    MAX_SINGLE_STOCK_WEIGHT,
    MAX_SPECULATIVE_WEIGHT,
    MIN_CONFIDENCE,
    MIN_TICKER_ALLOCATION,
    ROUNDING_STEP,
    Holding,
    InsightIn,
    _infer_conviction_level,
    _round_to_step,
    build_allocation_plan,
)


def _buy(
    ticker: str,
    *,
    conviction: str = "HIGH",
    confidence: float = 0.8,
    schema: str = "compact_v1",
    source: str = "live_llm",
    fallback: bool = False,
    stale: bool = False,
    why: str = "Strong earnings",
    do: str = "Add to position",
    category: str | None = None,
) -> InsightIn:
    return InsightIn(
        ticker=ticker,
        action="BUY",
        conviction_level=conviction,
        conviction_score=0.7 if conviction == "HIGH" else 0.5,
        confidence=confidence,
        schema_version=schema,
        analysis_source=source,
        used_fallback=fallback,
        stale=stale,
        category=category,
        why=why,
        do=do,
    )


# ── Rounding ─────────────────────────────────────────────────────────────────

class TestRounding:
    def test_round_to_nearest_5(self):
        assert _round_to_step(123.0) == 125.0
        assert _round_to_step(122.0) == 120.0
        assert _round_to_step(0.0) == 0.0
        assert _round_to_step(27.5) in (25.0, 30.0)  # banker's rounding edge

    def test_negative_becomes_zero(self):
        assert _round_to_step(-50) == 0.0


# ── Conviction inference ─────────────────────────────────────────────────────

class TestConvictionInference:
    def test_explicit_level(self):
        ins = InsightIn(ticker="X", action="BUY", conviction_level="HIGH")
        assert _infer_conviction_level(ins) == "HIGH"

    def test_score_high(self):
        ins = InsightIn(ticker="X", action="BUY", conviction_score=0.8)
        assert _infer_conviction_level(ins) == "HIGH"

    def test_score_medium(self):
        ins = InsightIn(ticker="X", action="BUY", conviction_score=0.4)
        assert _infer_conviction_level(ins) == "MEDIUM"

    def test_score_low(self):
        ins = InsightIn(ticker="X", action="BUY", conviction_score=0.1)
        assert _infer_conviction_level(ins) == "LOW"

    def test_weights_match_spec(self):
        assert CONVICTION_WEIGHTS["HIGH"] == 3
        assert CONVICTION_WEIGHTS["MEDIUM"] == 2
        assert CONVICTION_WEIGHTS["LOW"] == 1


# ── Exclusions ───────────────────────────────────────────────────────────────

class TestExclusions:
    def test_non_buy_excluded(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[
                InsightIn(ticker="AAPL", action="HOLD", confidence=0.9,
                          schema_version="compact_v1",
                          analysis_source="live_llm", why="x", do="x"),
            ],
        )
        assert plan.total_deployed == 0
        tickers_excluded = [e.ticker for e in plan.exclusions]
        assert "AAPL" in tickers_excluded

    def test_low_confidence_excluded(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[_buy("AAPL", confidence=0.5)],
        )
        reasons = {e.ticker: e.reason for e in plan.exclusions}
        assert "AAPL" in reasons
        assert "confidence" in reasons["AAPL"].lower()

    def test_fallback_excluded(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[_buy("AAPL", fallback=True)],
        )
        reasons = {e.ticker: e.reason for e in plan.exclusions}
        assert "fallback" in reasons["AAPL"].lower()

    def test_stale_excluded(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[_buy("AAPL", stale=True)],
        )
        reasons = {e.ticker: e.reason for e in plan.exclusions}
        assert "stale" in reasons["AAPL"].lower()

    def test_legacy_schema_excluded(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[_buy("AAPL", schema="legacy_v0")],
        )
        reasons = {e.ticker: e.reason for e in plan.exclusions}
        assert "schema" in reasons["AAPL"].lower()

    def test_wrong_source_excluded(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[_buy("AAPL", source="cached_run")],
        )
        reasons = {e.ticker: e.reason for e in plan.exclusions}
        assert "source" in reasons["AAPL"].lower()

    def test_missing_reasoning_excluded(self):
        ins = _buy("AAPL")
        ins.why = None
        ins.do = None
        ins.thesis = None
        plan = build_allocation_plan(
            cash_to_invest=900, holdings=[], insights=[ins],
        )
        reasons = {e.ticker: e.reason for e in plan.exclusions}
        assert "missing" in reasons["AAPL"].lower()

    def test_already_overweight_excluded(self):
        # AAPL at 25% of portfolio — above 20% single-stock cap
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[
                Holding(ticker="AAPL", market_value=2500, category="Core"),
                Holding(ticker="MSFT", market_value=7500, category="Core"),
            ],
            insights=[_buy("AAPL")],
        )
        reasons = {e.ticker: e.reason for e in plan.exclusions}
        assert "AAPL" in reasons
        assert "cap" in reasons["AAPL"].lower() or "target" in reasons["AAPL"].lower()

    def test_quota_blocked_excluded(self):
        ins = _buy("AAPL")
        ins.quota_blocked = True
        plan = build_allocation_plan(
            cash_to_invest=900, holdings=[], insights=[ins],
        )
        reasons = {e.ticker: e.reason for e in plan.exclusions}
        assert "quota" in reasons["AAPL"].lower()


# ── Scoring ──────────────────────────────────────────────────────────────────

class TestScoring:
    def test_high_conviction_beats_low(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[
                _buy("AAA", conviction="HIGH", confidence=0.8),
                _buy("BBB", conviction="LOW", confidence=0.8),
                _buy("CCC", conviction="MEDIUM", confidence=0.8),
            ],
        )
        # Rank order by score — AAA > CCC > BBB
        scores = {a.ticker: a.score for a in plan.allocations}
        assert scores["AAA"] > scores["CCC"] > scores["BBB"]

    def test_confidence_boost(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[
                _buy("AAA", conviction="HIGH", confidence=0.95),
                _buy("BBB", conviction="HIGH", confidence=0.70),
            ],
        )
        scores = {a.ticker: a.score for a in plan.allocations}
        assert scores["AAA"] > scores["BBB"]

    def test_volatility_penalty_on_crypto(self):
        # Crypto gets -0.75 volatility penalty vs Core
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[
                _buy("AAPL", conviction="HIGH", confidence=0.8, category="Core"),
                _buy("BTC", conviction="HIGH", confidence=0.8, category="Crypto"),
            ],
        )
        scores = {a.ticker: a.score for a in plan.allocations}
        assert scores["AAPL"] > scores["BTC"]


# ── Constraints ──────────────────────────────────────────────────────────────

class TestConstraints:
    def test_speculative_cap_5_pct(self):
        # $900 cash + $100 portfolio = $1000 after. Crypto cap = 5% = $50
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[Holding(ticker="VOO", market_value=100, category="ETF")],
            insights=[_buy("BTC", category="Crypto")],
        )
        if plan.allocations:
            total = plan.allocations[0].amount
            # BTC allocation should not exceed 5% of (100+900)=$50
            # BUT with $25 min threshold it may be 0 or a single small amount
            assert total <= 50 + ROUNDING_STEP

    def test_etf_cap_35_pct(self):
        # $900 + $100 = $1000 after. ETF cap = 35% = $350
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[Holding(ticker="AAPL", market_value=100, category="Core")],
            insights=[_buy("VOO", category="ETF", conviction="HIGH", confidence=0.9)],
        )
        if plan.allocations:
            assert plan.allocations[0].amount <= 350 + ROUNDING_STEP

    def test_single_stock_cap_20_pct(self):
        # $900 cash, $100 portfolio, single stock cap 20% of $1000 = $200
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[Holding(ticker="VOO", market_value=100, category="ETF")],
            insights=[_buy("AAPL", category="Core", conviction="HIGH", confidence=0.9)],
        )
        if plan.allocations:
            assert plan.allocations[0].amount <= 200 + ROUNDING_STEP

    def test_cap_values_match_spec(self):
        assert MAX_SINGLE_STOCK_WEIGHT == 20.0
        assert MAX_ETF_WEIGHT == 35.0
        assert MAX_SPECULATIVE_WEIGHT == 5.0
        assert MAX_SAME_THEME_WEIGHT == 40.0


# ── Allocation / rounding / totals ───────────────────────────────────────────

class TestAllocationTotals:
    def test_totals_equal_cash(self):
        """Acceptance criterion: Given $900 cash, exact dollar allocations
        that total exactly $900."""
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[
                Holding(ticker="VOO", market_value=5000, category="ETF"),
                Holding(ticker="AAPL", market_value=2000, category="Core"),
                Holding(ticker="MSFT", market_value=3000, category="Core"),
            ],
            insights=[
                _buy("NVDA", conviction="HIGH", confidence=0.9, category="Core"),
                _buy("AMD", conviction="HIGH", confidence=0.8, category="Core"),
                _buy("VOO", conviction="MEDIUM", confidence=0.75, category="ETF"),
                _buy("GOOGL", conviction="MEDIUM", confidence=0.7, category="Core"),
            ],
        )
        assert plan.total_deployed == 900.0
        assert plan.fully_allocated

    def test_all_amounts_rounded_to_5(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[
                Holding(ticker="VOO", market_value=5000, category="ETF"),
                Holding(ticker="AAPL", market_value=2000, category="Core"),
            ],
            insights=[
                _buy("NVDA", conviction="HIGH", confidence=0.9, category="Core"),
                _buy("AMD", conviction="HIGH", confidence=0.8, category="Core"),
                _buy("GOOGL", conviction="MEDIUM", confidence=0.75, category="Core"),
            ],
        )
        for a in plan.allocations:
            assert a.amount % ROUNDING_STEP == 0, (
                f"{a.ticker}: ${a.amount} not a multiple of ${ROUNDING_STEP}"
            )

    def test_min_ticker_allocation_enforced(self):
        # 20 tiny BUYs — after splitting $900, most would fall below $25 min
        insights = [
            _buy(f"T{i}", conviction="LOW", confidence=0.7)
            for i in range(20)
        ]
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[Holding(ticker="VOO", market_value=50000, category="ETF")],
            insights=insights,
        )
        for a in plan.allocations:
            assert a.amount >= MIN_TICKER_ALLOCATION

    def test_top_3_to_5_prioritized(self):
        # 8 candidates — only top 5 should get money
        insights = [
            _buy(f"T{i}", conviction="HIGH", confidence=0.85)
            for i in range(8)
        ]
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[Holding(ticker="VOO", market_value=50000, category="ETF")],
            insights=insights,
        )
        assert len(plan.allocations) <= 5

    def test_zero_cash_returns_empty_plan(self):
        plan = build_allocation_plan(
            cash_to_invest=0,
            holdings=[],
            insights=[_buy("AAPL")],
        )
        assert plan.total_deployed == 0
        assert not plan.fully_allocated


# ── Trims ────────────────────────────────────────────────────────────────────

class TestTrims:
    def test_trim_captured(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[Holding(ticker="NVDA", market_value=8000, category="Core"),
                      Holding(ticker="VOO", market_value=5000, category="ETF")],
            insights=[
                InsightIn(
                    ticker="NVDA", action="TRIM",
                    conviction_level="HIGH", confidence=0.8,
                    schema_version="compact_v1", analysis_source="live_llm",
                    why="Overweight", risk="Concentration", do="Trim 10%",
                ),
                _buy("AMD", conviction="HIGH", confidence=0.9),
            ],
        )
        trim_tickers = [t.ticker for t in plan.trims]
        assert "NVDA" in trim_tickers


# ── Safety / warnings ────────────────────────────────────────────────────────

class TestSafety:
    def test_fewer_than_two_eligible_warns(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[_buy("AAPL")],   # only 1 eligible
        )
        assert plan.warning is not None
        assert "not enough fresh high-confidence" in plan.warning.lower()

    def test_no_eligible_empty_allocations(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[],
            insights=[
                _buy("A", fallback=True),
                _buy("B", confidence=0.3),
                _buy("C", stale=True),
            ],
        )
        assert plan.allocations == []
        assert plan.warning is not None

    def test_min_confidence_constant(self):
        assert MIN_CONFIDENCE == 0.65


# ── Weight before/after ──────────────────────────────────────────────────────

class TestBeforeAfterWeight:
    def test_after_weight_reflects_deployment(self):
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[
                Holding(ticker="NVDA", market_value=1000, category="Core"),
                Holding(ticker="VOO", market_value=9000, category="ETF"),
            ],
            insights=[
                _buy("NVDA", conviction="HIGH", confidence=0.9, category="Core"),
                _buy("AMD", conviction="HIGH", confidence=0.85, category="Core"),
                _buy("GOOGL", conviction="MEDIUM", confidence=0.8, category="Core"),
            ],
        )
        for a in plan.allocations:
            # before weight is a percentage in [0, 100]
            assert 0 <= a.current_weight <= 100
            # after weight must be >= current weight if we added money
            if a.amount > 0:
                assert a.after_weight >= a.current_weight - 0.01


# ── Acceptance — $900 end-to-end ─────────────────────────────────────────────

class TestAcceptance:
    def test_900_dollars_exact_deployment(self):
        """Given $900 cash + realistic inputs, deploy shows exact $s that
        total exactly $900, using only fresh high-confidence BUYs."""
        plan = build_allocation_plan(
            cash_to_invest=900,
            holdings=[
                Holding(ticker="NVDA", market_value=1000, category="Core"),
                Holding(ticker="VOO", market_value=8000, category="ETF"),
                Holding(ticker="AAPL", market_value=2000, category="Core"),
                Holding(ticker="BTC", market_value=300, category="Crypto"),
            ],
            insights=[
                _buy("NVDA", conviction="HIGH", confidence=0.9),
                _buy("AMD", conviction="HIGH", confidence=0.85),
                _buy("GOOGL", conviction="MEDIUM", confidence=0.75),
                _buy("VOO", conviction="MEDIUM", confidence=0.7, category="ETF"),
                _buy("MSFT", conviction="LOW", confidence=0.68),
                # These must be filtered out:
                _buy("STALE", stale=True),
                _buy("FALLBACK", fallback=True),
                _buy("LOWCONF", confidence=0.4),
            ],
        )
        # Totals exactly $900
        assert plan.total_deployed == 900.0
        assert plan.fully_allocated

        # Stale / fallback / low-conf excluded
        excluded = {e.ticker for e in plan.exclusions}
        assert "STALE" in excluded
        assert "FALLBACK" in excluded
        assert "LOWCONF" in excluded

        # Only top eligible ideas receive money
        assert 1 <= len(plan.allocations) <= 5

        # Strategy explanation exists
        assert plan.portfolio_explanation
        assert plan.strategy
