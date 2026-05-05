"""Table-driven tests for the Intel v3 decision policy kernel.

Dark launch — backend only. Tests confirm deterministic behavior.

Coverage:
1. Strong evidence + attractive + fair/cheap + fit OK → BUY
2. Thin evidence + otherwise attractive → HOLD LOW with suppression reason
3. Overweight/breach fit → TRIM
4. Critical risk → SELL or TRIM depending on severity, never BUY
5. Price SUPPRESSED + strong evidence → BUY MEDIUM max (never HIGH)
6. Mixed-signal fixture → ≥2 distinct actions or ≥2 distinct convictions
7. Unknown/legacy action labels → must not produce illegal action categories
8. Output rationale must not contain raw metric key names
9. No BUY when evidence_quality is THIN
10. Adapter: build from card signals smoke test
11. Adapter: SUPPRESSED evidence when no intel_read / no quality label
12. Adapter: THIN from intel_read.insufficient_data=True
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionInputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from app.services.intelligence.v3.decision_policy_v1 import decide
from app.services.intelligence.v3.existing_signal_adapter import (
    build_decision_input_from_card,
)

# Raw metric keys that must not appear in decision output text.
_RAW_METRIC_KEYS = [
    "fcf_margin",
    "roic_ttm",
    "p_fcf",
    "fcf_yield",
    "gross_margin",
    "net_debt_to_ebitda",
    "ev_ebitda",
    "revenue_cagr_3y",
    "max_drawdown_1y",
    "trailing_pe",
    "forward_pe",
    "momentum_score",
    "valuation_score",
    "quality_score",
    "growth_score",
    "risk_score",
]

_VALID_ACTIONS = {a.value for a in ActionV3}  # BUY, HOLD, TRIM, SELL


def _no_raw_keys(text: str) -> bool:
    lower = text.lower()
    return not any(key in lower for key in _RAW_METRIC_KEYS)


# ── Test 1: Strong evidence + fair/cheap price + good fit → BUY ──────────────

class TestBuyConditions:
    def test_strong_evidence_fair_price_on_target_buy(self):
        inp = DecisionInputV3(
            ticker="AAPL",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.LOW,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction in {ConvictionV3.HIGH, ConvictionV3.MEDIUM}

    def test_strong_evidence_cheap_price_underweight_buy(self):
        inp = DecisionInputV3(
            ticker="MSFT",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.CHEAP,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY

    def test_ok_evidence_fair_price_medium_conviction_buy(self):
        inp = DecisionInputV3(
            ticker="GOOGL",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="MEDIUM",
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY


# ── Test 2 & 9: Thin evidence forbids BUY, caps conviction at LOW ─────────────

class TestThinEvidence:
    def test_thin_evidence_attractive_produces_hold_low(self):
        inp = DecisionInputV3(
            ticker="NVDA",
            evidence_quality=AxisBand.THIN,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
            suppression_reasons={"evidence_quality": "Insufficient data — 0 trusted dimensions."},
        )
        out = decide(inp)
        assert out.action == ActionV3.HOLD, "Thin evidence must not produce BUY"
        assert out.conviction == ConvictionV3.LOW, "Thin evidence must cap conviction at LOW"
        # Must carry suppression context
        assert out.suppression_reasons or out.blockers

    def test_no_buy_when_evidence_thin_cheap_price(self):
        """Acceptance criterion 9: No BUY when evidence_quality is THIN."""
        inp = DecisionInputV3(
            ticker="AMD",
            evidence_quality=AxisBand.THIN,
            price_context=PriceBand.CHEAP,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="MEDIUM",
        )
        out = decide(inp)
        assert out.action != ActionV3.BUY

    def test_suppressed_evidence_no_buy(self):
        inp = DecisionInputV3(
            ticker="STUB",
            evidence_quality=AxisBand.SUPPRESSED,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action != ActionV3.BUY


# ── Test 3: Overweight/breach fit → TRIM ────────────────────────────────────

class TestTrimConditions:
    def test_overweight_fit_trim(self):
        inp = DecisionInputV3(
            ticker="META",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.FULL,
            portfolio_fit=FitBand.OVERWEIGHT,
            risk_band=RiskBand.MEDIUM,
            raw_action="TRIM",
            raw_analyst_action="TRIM",
            upstream_conviction="MEDIUM",
        )
        out = decide(inp)
        assert out.action == ActionV3.TRIM

    def test_breach_fit_trim_when_not_critical(self):
        inp = DecisionInputV3(
            ticker="SNOW",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.BREACH,
            risk_band=RiskBand.MEDIUM,
            raw_action="HOLD",
            upstream_conviction="LOW",
        )
        out = decide(inp)
        assert out.action == ActionV3.TRIM

    def test_high_risk_reduce_signal_trim(self):
        inp = DecisionInputV3(
            ticker="RDDT",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FULL,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.HIGH,
            raw_action="TRIM",
            raw_analyst_action="TRIM",
            upstream_conviction="LOW",
        )
        out = decide(inp)
        assert out.action == ActionV3.TRIM


# ── Test 4: Critical risk → SELL or TRIM, never BUY ────────────────────────

class TestCriticalRisk:
    def test_critical_risk_sell_signal_produces_sell(self):
        inp = DecisionInputV3(
            ticker="BTC",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.EXPENSIVE,
            portfolio_fit=FitBand.BREACH,
            risk_band=RiskBand.CRITICAL,
            raw_action="SELL",
            raw_analyst_action="SELL",
            upstream_conviction="LOW",
        )
        out = decide(inp)
        assert out.action == ActionV3.SELL

    def test_critical_risk_breach_produces_sell(self):
        inp = DecisionInputV3(
            ticker="XRP",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.EXPENSIVE,
            portfolio_fit=FitBand.BREACH,
            risk_band=RiskBand.CRITICAL,
            raw_action="HOLD",
            upstream_conviction="LOW",
        )
        out = decide(inp)
        assert out.action == ActionV3.SELL

    def test_critical_risk_no_breach_overweight_trim(self):
        """Critical risk + OVERWEIGHT (no breach) → TRIM path via rule 2."""
        inp = DecisionInputV3(
            ticker="RIVN",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FULL,
            portfolio_fit=FitBand.OVERWEIGHT,
            risk_band=RiskBand.CRITICAL,
            raw_action="TRIM",
            upstream_conviction="LOW",
        )
        out = decide(inp)
        # Rule 2 fires before Rule 1 check for breach; TRIM is acceptable
        assert out.action in {ActionV3.SELL, ActionV3.TRIM}

    def test_critical_risk_never_buy(self):
        """Critical risk must never produce BUY regardless of other signals."""
        inp = DecisionInputV3(
            ticker="KLAR",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.CRITICAL,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action != ActionV3.BUY, "Critical risk must never produce BUY"


# ── Test 5: Price SUPPRESSED + strong evidence → BUY MEDIUM max ─────────────

class TestPriceSuppressed:
    def test_price_suppressed_strong_evidence_buy_capped_medium(self):
        inp = DecisionInputV3(
            ticker="GOOGL",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.SUPPRESSED,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction != ConvictionV3.HIGH, (
            "Price SUPPRESSED must cap conviction — not HIGH"
        )
        assert out.conviction == ConvictionV3.MEDIUM

    def test_price_suppressed_ok_evidence_no_buy(self):
        """OK evidence + suppressed price is not strong enough for BUY (needs STRONG+STRONG)."""
        inp = DecisionInputV3(
            ticker="CRM",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.SUPPRESSED,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        # OK evidence + suppressed price → HOLD (rule 3 requires STRONG+STRONG for this path)
        assert out.action == ActionV3.HOLD


# ── Test 6: Mixed fixture → ≥2 distinct actions or ≥2 distinct convictions ───

class TestMixedFixture:
    def test_mixed_signals_produce_differentiated_output(self):
        """Non-degenerate mix must not collapse to all HOLD/LOW."""
        fixtures = [
            DecisionInputV3(
                ticker="AAPL",
                evidence_quality=AxisBand.STRONG,
                price_context=PriceBand.FAIR,
                portfolio_fit=FitBand.ON_TARGET,
                risk_band=RiskBand.NONE,
                raw_action="BUY",
                raw_analyst_action="BUY",
                upstream_conviction="HIGH",
            ),
            DecisionInputV3(
                ticker="META",
                evidence_quality=AxisBand.STRONG,
                price_context=PriceBand.FULL,
                portfolio_fit=FitBand.OVERWEIGHT,
                risk_band=RiskBand.MEDIUM,
                raw_action="TRIM",
                raw_analyst_action="TRIM",
                upstream_conviction="MEDIUM",
            ),
            DecisionInputV3(
                ticker="NVDA",
                evidence_quality=AxisBand.THIN,
                price_context=PriceBand.FAIR,
                portfolio_fit=FitBand.UNDERWEIGHT,
                risk_band=RiskBand.NONE,
                raw_action="BUY",
                raw_analyst_action="BUY",
                upstream_conviction="LOW",
                suppression_reasons={"evidence_quality": "Only 1 trusted dimension."},
            ),
            DecisionInputV3(
                ticker="BTC",
                evidence_quality=AxisBand.OK,
                price_context=PriceBand.EXPENSIVE,
                portfolio_fit=FitBand.BREACH,
                risk_band=RiskBand.CRITICAL,
                raw_action="SELL",
                raw_analyst_action="SELL",
                upstream_conviction="LOW",
            ),
        ]
        outputs = [decide(f) for f in fixtures]
        distinct_actions = {o.action for o in outputs}
        distinct_convictions = {o.conviction for o in outputs}
        assert len(distinct_actions) >= 2 or len(distinct_convictions) >= 2, (
            f"Expected differentiated output, got "
            f"actions={distinct_actions}, convictions={distinct_convictions}"
        )


# ── Test 7: Unknown/legacy labels must not produce illegal action categories ──

class TestLegacyLabels:
    def test_legacy_review_watchlist_labels_produce_valid_actions(self):
        """REVIEW, WATCHLIST, ADD_CANDIDATE etc. must not appear as output actions."""
        legacy_inputs = [
            DecisionInputV3(
                ticker="XYZ",
                evidence_quality=AxisBand.OK,
                price_context=PriceBand.FAIR,
                portfolio_fit=FitBand.ON_TARGET,
                risk_band=RiskBand.LOW,
                raw_action="REVIEW",
                raw_analyst_action="WATCHLIST",
                upstream_conviction="MEDIUM",
            ),
            DecisionInputV3(
                ticker="ABC",
                evidence_quality=AxisBand.SUPPRESSED,
                price_context=PriceBand.SUPPRESSED,
                portfolio_fit=FitBand.UNKNOWN,
                risk_band=RiskBand.UNKNOWN,
                raw_action="ADD_CANDIDATE",
                raw_analyst_action="RISK_WATCH",
                upstream_conviction=None,
            ),
        ]
        _forbidden = {"REVIEW", "WATCHLIST", "ADD_CANDIDATE", "RISK_WATCH", "ADD", "REDUCE"}
        for inp in legacy_inputs:
            out = decide(inp)
            assert out.action.value in _VALID_ACTIONS, (
                f"Illegal action {out.action!r} for {inp.ticker}"
            )
            assert out.action.value not in _forbidden, (
                f"Legacy label leaked into output: {out.action!r}"
            )


# ── Test 8: Rationale must not contain raw metric key names ──────────────────

class TestRationaleClean:
    @pytest.mark.parametrize("inp", [
        DecisionInputV3(
            ticker="AAPL",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            upstream_conviction="HIGH",
        ),
        DecisionInputV3(
            ticker="META",
            evidence_quality=AxisBand.THIN,
            price_context=PriceBand.SUPPRESSED,
            portfolio_fit=FitBand.OVERWEIGHT,
            risk_band=RiskBand.HIGH,
            raw_action="TRIM",
            upstream_conviction="LOW",
        ),
        DecisionInputV3(
            ticker="HOLD_ALL",
            evidence_quality=AxisBand.SUPPRESSED,
            price_context=PriceBand.SUPPRESSED,
            portfolio_fit=FitBand.UNKNOWN,
            risk_band=RiskBand.UNKNOWN,
            raw_action="HOLD",
            upstream_conviction=None,
        ),
        DecisionInputV3(
            ticker="BTC",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.EXPENSIVE,
            portfolio_fit=FitBand.BREACH,
            risk_band=RiskBand.CRITICAL,
            raw_action="SELL",
            upstream_conviction="LOW",
        ),
    ])
    def test_no_raw_metric_keys_in_rationale(self, inp: DecisionInputV3):
        out = decide(inp)
        for field_text in [out.rationale_plain_english, out.why_now, out.why_not_now]:
            assert _no_raw_keys(field_text), (
                f"Raw metric key found in output for {inp.ticker}: {field_text!r}"
            )


# ── Adapter integration tests ──────────────────────────────────────────────

class TestAdapter:
    def test_build_from_card_signals_smoke(self):
        """Adapter builds DecisionInputV3 without error on typical BUY card."""
        inp = build_decision_input_from_card(
            ticker="NVDA",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=[],
            category="Tech",
            data_quality_label="HIGH",
            intel_read={
                "insufficient_data": False,
                "trusted_dimensions": ["business quality", "valuation", "growth"],
                "suppressed_dimensions": [],
            },
            thesis_v2=None,
        )
        out = decide(inp)
        assert out.action in {ActionV3.BUY, ActionV3.HOLD}
        assert out.ticker == "NVDA"
        assert out.schema_version == "v3.1"

    def test_suppressed_evidence_when_no_signals(self):
        """SUPPRESSED evidence when no intel_read and no data_quality_label."""
        inp = build_decision_input_from_card(
            ticker="KLAR",
            action="HOLD",
            analyst_action=None,
            conviction_level=None,
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category=None,
            data_quality_label=None,
            intel_read=None,
            thesis_v2=None,
        )
        assert inp.evidence_quality == AxisBand.SUPPRESSED
        assert "evidence_quality" in inp.suppression_reasons

    def test_thin_evidence_from_intel_read_insufficient(self):
        """intel_read.insufficient_data=True → THIN evidence → no BUY."""
        inp = build_decision_input_from_card(
            ticker="SNOW",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=[],
            category="Tech",
            data_quality_label="HIGH",
            intel_read={
                "insufficient_data": True,
                "trusted_dimensions": [],
                "suppressed_dimensions": ["valuation", "growth"],
            },
            thesis_v2=None,
        )
        assert inp.evidence_quality == AxisBand.THIN
        out = decide(inp)
        assert out.action != ActionV3.BUY, "Thin evidence must not produce BUY"

    def test_adapter_speculative_ticker_blocked_fit(self):
        """Speculative tickers get BLOCKED portfolio fit."""
        inp = build_decision_input_from_card(
            ticker="BTC",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=[],
            category="Crypto",
            data_quality_label="MEDIUM",
            intel_read=None,
            thesis_v2=None,
        )
        assert inp.portfolio_fit == FitBand.BLOCKED
        # BLOCKED fit must not produce BUY
        out = decide(inp)
        assert out.action != ActionV3.BUY

    def test_adapter_sell_card_produces_sell_signals(self):
        """SELL action card → EXPENSIVE price + BREACH fit in adapter output."""
        inp = build_decision_input_from_card(
            ticker="CAVA",
            action="SELL",
            analyst_action="SELL",
            conviction_level="HIGH",
            technical_signal="BEARISH",
            risk_flag="Thesis broken — growth decelerating",
            analyst_risks=["Revenue growth stalled", "Valuation unsustainable"],
            category="Growth",
            data_quality_label="HIGH",
            intel_read={
                "insufficient_data": False,
                "trusted_dimensions": ["business quality", "risk"],
                "suppressed_dimensions": [],
            },
            thesis_v2=None,
        )
        assert inp.price_context == PriceBand.EXPENSIVE
        assert inp.portfolio_fit == FitBand.BREACH
        assert inp.risk_band in {RiskBand.HIGH, RiskBand.CRITICAL, RiskBand.MEDIUM}

    def test_adapter_trim_card(self):
        """TRIM action card → FULL price + OVERWEIGHT fit."""
        inp = build_decision_input_from_card(
            ticker="NFLX",
            action="TRIM",
            analyst_action="TRIM",
            conviction_level="MEDIUM",
            technical_signal=None,
            risk_flag=None,
            analyst_risks=[],
            category="Growth",
            data_quality_label="MEDIUM",
            intel_read=None,
            thesis_v2=None,
        )
        assert inp.price_context == PriceBand.FULL
        assert inp.portfolio_fit == FitBand.OVERWEIGHT
        out = decide(inp)
        assert out.action == ActionV3.TRIM
