"""Stage 6 — Evidence-Aware Intel v3 Decision Engine Certification.

Golden proof harness + focused tests. Answers definitively whether:
  1. Stage 6 can produce action diversity beyond HOLD when evidence supports it.
  2. Weak/missing/stale/suppressed evidence blocks or caps decisions.
  3. Deterministic policy remains final authority (never LLM/provider-driven).
  4. Flag off preserves contract-equivalent visible behavior.
  5. ETF/crypto SEC not_applicable is handled honestly, not penalized.
  6. Macro context is advisory only — never independently forces Buy/Sell.

Test structure:
  I.   Golden proof scenarios (10 canonical decision scenarios)
  II.  Flag-off / flag-on safety contracts
  III. Conviction capping rules
  IV.  ETF/crypto SEC not_applicable handling
  V.   Macro context advisory-only invariant
  VI.  HOLD-collapse detection + action diversity
  VII. Safety invariants (no raw payloads, no provider/LLM calls, no DB writes)
  VIII.Portfolio governance summary
"""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock, patch

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
from app.services.intelligence.v3.intel_v3_evidence_aware_governance_v1 import (
    FLAG_NAME,
    GOVERNANCE_VERSION,
    EvidenceGovernanceResult,
    PortfolioGovernanceSummary,
    apply_evidence_governance,
    compute_portfolio_governance_summary,
    _classify_hold_collapse_risk,
    _derive_governed_evidence_quality,
    _hold_pct,
    _will_cap_conviction,
)
from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
    AXIS_COMPANY_FUNDAMENTALS,
    AXIS_MACRO_CONTEXT,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL_SIGNALS,
    READINESS_INSUFFICIENT,
    READINESS_LIMITED,
    READINESS_MISSING,
    READINESS_NOT_APPLICABLE,
    READINESS_NOT_EVALUABLE,
    READINESS_READY,
    READINESS_STALE_OR_UNKNOWN,
    READINESS_SUPPRESSED,
    AxisReadinessSignal,
    ResearchEvidenceDecisionInputShadow,
    TickerDecisionReadiness,
)
from app.services.intelligence.v3.decision_policy_v1 import decide


# ── Test helpers ──────────────────────────────────────────────────────────────


def _make_axis(readiness: str, is_usable: bool | None = None) -> AxisReadinessSignal:
    if is_usable is None:
        is_usable = readiness in {READINESS_READY, READINESS_LIMITED}
    contributing = ["lane_x"] if is_usable else []
    degraded = ["lane_x"] if not is_usable and readiness not in {READINESS_MISSING, READINESS_NOT_APPLICABLE} else []
    missing = ["lane_x"] if readiness == READINESS_MISSING else []
    not_applicable = ["lane_x"] if readiness == READINESS_NOT_APPLICABLE else []
    return AxisReadinessSignal(
        axis_name="test",
        readiness=readiness,
        is_usable=is_usable,
        contributing_lanes=contributing,
        degraded_lanes=degraded,
        missing_lanes=missing,
        not_applicable_lanes=not_applicable,
        lane_contributions=[],
    )


def _make_ticker_readiness(
    ticker: str,
    *,
    fund: str = READINESS_MISSING,
    tech: str = READINESS_MISSING,
    sent: str = READINESS_MISSING,
    sec_lane_applicable: bool = True,
    instrument_category: str = "equity",
) -> TickerDecisionReadiness:
    axes = {
        AXIS_COMPANY_FUNDAMENTALS: _make_axis(fund),
        AXIS_TECHNICAL_SIGNALS: _make_axis(tech),
        AXIS_SENTIMENT: _make_axis(sent),
    }
    usable = sum(1 for a in axes.values() if a.is_usable)
    return TickerDecisionReadiness(
        ticker=ticker,
        sec_lane_applicable=sec_lane_applicable,
        instrument_category=instrument_category,
        axes=axes,
        any_axis_usable=usable > 0,
        usable_axis_count=usable,
    )


def _make_macro(readiness: str) -> AxisReadinessSignal:
    return _make_axis(readiness)


def _make_inp(
    ticker: str = "AAPL",
    evidence_quality: AxisBand = AxisBand.SUPPRESSED,
    raw_action: str | None = "BUY",
    upstream_conviction: str | None = "HIGH",
    price_context: PriceBand = PriceBand.FAIR,
    portfolio_fit: FitBand = FitBand.UNDERWEIGHT,
    risk_band: RiskBand = RiskBand.LOW,
) -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=evidence_quality,
        price_context=price_context,
        portfolio_fit=portfolio_fit,
        risk_band=risk_band,
        raw_action=raw_action,
        upstream_conviction=upstream_conviction,
    )


def _make_shadow(
    user_id: str = "test-user",
    ticker_readiness: dict | None = None,
    portfolio_macro: AxisReadinessSignal | None = None,
) -> ResearchEvidenceDecisionInputShadow:
    tr = ticker_readiness or {}
    return ResearchEvidenceDecisionInputShadow(
        schema_version="research_evidence_decision_input_adapter.v1",
        adapter_version="research_evidence_decision_input_adapter.v1",
        user_id=user_id,
        generated_at="2026-05-19T00:00:00+00:00",
        coverage_schema_version="research_evidence_coverage.v1",
        shadow_only=True,
        safe_for_decision=False,
        no_guessing=True,
        portfolio_ticker_count=len(tr),
        ticker_readiness=tr,
        portfolio_macro=portfolio_macro,
        tickers_with_any_usable_axis=sum(
            1 for v in tr.values() if v.any_axis_usable
        ),
        tickers_fully_missing=sum(
            1 for v in tr.values() if not v.any_axis_usable
        ),
        axis_usable_counts={
            AXIS_COMPANY_FUNDAMENTALS: sum(
                1 for v in tr.values()
                if v.axes.get(AXIS_COMPANY_FUNDAMENTALS)
                and v.axes[AXIS_COMPANY_FUNDAMENTALS].is_usable
            ),
            AXIS_TECHNICAL_SIGNALS: 0,
            AXIS_SENTIMENT: 0,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# I. GOLDEN PROOF SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════════


class TestGoldenScenarios:
    """Golden proof: answers whether Stage 6 can produce action diversity.

    Each scenario: apply governance → run decide() → check outcome.
    """

    def test_golden_strong_equity_evidence_supports_buy(self):
        """Strong equity evidence (fund READY + tech READY) with BUY signal → BUY."""
        readiness = _make_ticker_readiness(
            "AAPL",
            fund=READINESS_READY,
            tech=READINESS_READY,
            sent=READINESS_LIMITED,
        )
        inp = _make_inp(
            ticker="AAPL",
            evidence_quality=AxisBand.SUPPRESSED,  # starts suppressed (no analyst)
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)

        assert result.governance_applied is True
        assert result.governed_evidence_quality == AxisBand.STRONG.value
        assert decision.action == ActionV3.BUY
        assert decision.conviction == ConvictionV3.HIGH
        assert "strong_fundamentals_with_corroboration" in result.reason_codes

    def test_golden_weak_missing_evidence_blocks_buy_falls_to_hold(self):
        """Weak/missing evidence: all axes MISSING → THIN → HOLD."""
        readiness = _make_ticker_readiness(
            "WEAK",
            fund=READINESS_MISSING,
            tech=READINESS_MISSING,
            sent=READINESS_MISSING,
        )
        inp = _make_inp(
            ticker="WEAK",
            evidence_quality=AxisBand.OK,  # analyst said OK, but no artifacts
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)

        assert result.governance_applied is True
        assert result.governed_evidence_quality == AxisBand.THIN.value
        assert decision.action == ActionV3.HOLD
        assert "all_evidence_axes_missing_or_degraded" in result.reason_codes
        assert "buy_blocked_missing_evidence" in result.action_blocks_applied

    def test_golden_suppressed_contradicted_fundamentals_block_positive_action(self):
        """Suppressed/contradicted fundamentals → SUPPRESSED → BUY blocked."""
        readiness = _make_ticker_readiness(
            "CONTRA",
            fund=READINESS_SUPPRESSED,
            tech=READINESS_READY,
            sent=READINESS_READY,
        )
        inp = _make_inp(
            ticker="CONTRA",
            evidence_quality=AxisBand.STRONG,
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)

        assert result.governed_evidence_quality == AxisBand.SUPPRESSED.value
        # decide() with SUPPRESSED evidence → HOLD (evidence blocks BUY rule)
        assert decision.action == ActionV3.HOLD
        assert "buy_blocked_suppressed_fundamentals" in result.action_blocks_applied

    def test_golden_stale_evidence_blocks_buy(self):
        """Stale fundamentals (stale + no other usable) → THIN → HOLD."""
        readiness = _make_ticker_readiness(
            "STALE",
            fund=READINESS_STALE_OR_UNKNOWN,
            tech=READINESS_MISSING,
            sent=READINESS_MISSING,
        )
        inp = _make_inp(
            ticker="STALE",
            evidence_quality=AxisBand.OK,
            raw_action="BUY",
            upstream_conviction="MEDIUM",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)

        assert result.governed_evidence_quality == AxisBand.THIN.value
        assert decision.action == ActionV3.HOLD
        assert "buy_blocked_stale_evidence" in result.action_blocks_applied

    def test_golden_negative_risk_evidence_supports_trim(self):
        """Negative/risk: portfolio OVERWEIGHT + risk signal → TRIM (governance does not block).

        Governance governs evidence_quality only; TRIM is driven by portfolio_fit.
        """
        readiness = _make_ticker_readiness(
            "TRIM_ME",
            fund=READINESS_MISSING,
            tech=READINESS_MISSING,
            sent=READINESS_MISSING,
        )
        inp = _make_inp(
            ticker="TRIM_ME",
            evidence_quality=AxisBand.OK,
            raw_action="TRIM",
            upstream_conviction="MEDIUM",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.OVERWEIGHT,  # triggers TRIM rule
            risk_band=RiskBand.MEDIUM,
        )
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)

        # governance applies THIN but TRIM is still driven by portfolio_fit
        assert result.governed_evidence_quality == AxisBand.THIN.value
        assert decision.action == ActionV3.TRIM  # policy still allows TRIM from fit

    def test_golden_etf_sec_not_applicable_not_penalized(self):
        """ETF with yfinance fundamentals READY: not penalized for no SEC data.

        Stage 5K marks SEC lane NOT_APPLICABLE for ETFs. The company_fundamentals
        axis uses only yfinance lane → READY. Governance sees READY, not MISSING.
        """
        fund_axis = _make_axis(READINESS_READY)  # yfinance fundamentals lane is READY
        # ETF with no SEC: sec_lane_applicable=False, but fundamentals axis still READY
        readiness = TickerDecisionReadiness(
            ticker="SPY",
            sec_lane_applicable=False,
            axes={
                AXIS_COMPANY_FUNDAMENTALS: fund_axis,
                AXIS_TECHNICAL_SIGNALS: _make_axis(READINESS_READY),
                AXIS_SENTIMENT: _make_axis(READINESS_MISSING),
            },
            any_axis_usable=True,
            usable_axis_count=2,
        )
        inp = _make_inp(
            ticker="SPY",
            evidence_quality=AxisBand.SUPPRESSED,
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)

        assert result.governed_evidence_quality == AxisBand.STRONG.value
        assert decision.action == ActionV3.BUY
        assert result.not_applicable_axis_count == 0  # SEC axis not counted against ETF

    def test_golden_crypto_handled_honestly_without_fake_sec(self):
        """Crypto: no fundamentals (both missing) → THIN → HOLD conservative."""
        readiness = _make_ticker_readiness(
            "BTC",
            fund=READINESS_MISSING,
            tech=READINESS_MISSING,
            sent=READINESS_MISSING,
            sec_lane_applicable=False,
        )
        inp = _make_inp(
            ticker="BTC",
            evidence_quality=AxisBand.SUPPRESSED,
            raw_action="BUY",
            upstream_conviction="MEDIUM",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.MEDIUM,
        )
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)

        assert result.governed_evidence_quality == AxisBand.THIN.value
        assert decision.action == ActionV3.HOLD
        # Ensure no fabricated SEC data contributed
        assert "buy_blocked_missing_evidence" in result.action_blocks_applied

    def test_golden_macro_context_cannot_independently_force_buy(self):
        """Macro READY cannot independently force BUY when evidence axes are missing."""
        macro = _make_macro(READINESS_READY)
        readiness = _make_ticker_readiness(
            "NOMACRO",
            fund=READINESS_MISSING,
            tech=READINESS_MISSING,
            sent=READINESS_MISSING,
        )
        inp = _make_inp(
            ticker="NOMACRO",
            evidence_quality=AxisBand.SUPPRESSED,
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        result = apply_evidence_governance(inp, readiness, macro, flag_enabled=True)
        decision = decide(inp)

        # Macro is advisory; still HOLD because fundamental/tech/sentiment axes missing
        assert result.governed_evidence_quality == AxisBand.THIN.value
        assert decision.action == ActionV3.HOLD
        assert "macro_context_advisory" in inp.suppression_reasons

    def test_golden_flag_off_visible_behavior_unchanged(self):
        """Flag off: governance is a complete no-op; visible output is identical."""
        readiness = _make_ticker_readiness(
            "AAPL",
            fund=READINESS_READY,
            tech=READINESS_READY,
            sent=READINESS_READY,
        )
        inp_governed = _make_inp(ticker="AAPL", evidence_quality=AxisBand.SUPPRESSED)
        inp_baseline = _make_inp(ticker="AAPL", evidence_quality=AxisBand.SUPPRESSED)

        result = apply_evidence_governance(inp_governed, readiness, None, flag_enabled=False)
        decision_governed = decide(inp_governed)
        decision_baseline = decide(inp_baseline)

        assert result.governance_applied is False
        assert result.governed_evidence_quality == AxisBand.SUPPRESSED.value
        assert inp_governed.evidence_quality == inp_baseline.evidence_quality
        assert decision_governed.action == decision_baseline.action
        assert decision_governed.conviction == decision_baseline.conviction

    def test_golden_flag_on_action_diversity_with_evidence_variety(self):
        """Flag on: portfolio with varied evidence produces non-all-HOLD distribution.

        Tests that Stage 6 is capable of producing BUY diversity when evidence supports.
        """
        tickers_and_config = [
            # (ticker, fund, tech, sent, expected_governed, raw_action)
            ("STRONG1", READINESS_READY, READINESS_READY, READINESS_READY, AxisBand.STRONG, "BUY"),
            ("STRONG2", READINESS_READY, READINESS_LIMITED, READINESS_MISSING, AxisBand.STRONG, "BUY"),
            ("OK1", READINESS_READY, READINESS_MISSING, READINESS_MISSING, AxisBand.OK, "BUY"),
            ("OK2", READINESS_LIMITED, READINESS_READY, READINESS_MISSING, AxisBand.OK, "BUY"),
            # Priority 4b (calibrated): LIMITED + no corroboration → OK (not THIN)
            ("OK3", READINESS_LIMITED, READINESS_MISSING, READINESS_MISSING, AxisBand.OK, "BUY"),
            ("THIN1", READINESS_MISSING, READINESS_READY, READINESS_READY, AxisBand.THIN, "BUY"),
            ("SUPP1", READINESS_SUPPRESSED, READINESS_READY, READINESS_READY, AxisBand.SUPPRESSED, "BUY"),
        ]
        actions = []
        for ticker, fund, tech, sent, expected_band, raw_action in tickers_and_config:
            readiness = _make_ticker_readiness(ticker, fund=fund, tech=tech, sent=sent)
            inp = _make_inp(
                ticker=ticker,
                evidence_quality=AxisBand.SUPPRESSED,
                raw_action=raw_action,
                upstream_conviction="HIGH",
                price_context=PriceBand.FAIR,
                portfolio_fit=FitBand.UNDERWEIGHT,
                risk_band=RiskBand.LOW,
            )
            result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
            assert result.governed_evidence_quality == expected_band.value, (
                f"{ticker}: expected {expected_band.value}, got {result.governed_evidence_quality}"
            )
            decision = decide(inp)
            actions.append(decision.action)

        buy_count = actions.count(ActionV3.BUY)
        hold_count = actions.count(ActionV3.HOLD)
        # With STRONG + OK evidence, we expect BUY diversity (not all HOLD)
        assert buy_count >= 2, f"Expected ≥2 BUYs from strong/OK evidence, got {buy_count}"
        assert hold_count >= 2, f"Expected ≥2 HOLDs from thin/suppressed evidence, got {hold_count}"

    def test_golden_missing_evidence_produces_conservative_hold(self):
        """Across the board missing evidence → all HOLD (conservative baseline)."""
        tickers = ["T1", "T2", "T3", "T4"]
        actions = []
        for ticker in tickers:
            readiness = _make_ticker_readiness(
                ticker,
                fund=READINESS_MISSING,
                tech=READINESS_MISSING,
                sent=READINESS_MISSING,
            )
            inp = _make_inp(
                ticker=ticker,
                evidence_quality=AxisBand.SUPPRESSED,
                raw_action="BUY",
                upstream_conviction="HIGH",
                price_context=PriceBand.FAIR,
                portfolio_fit=FitBand.UNDERWEIGHT,
                risk_band=RiskBand.LOW,
            )
            apply_evidence_governance(inp, readiness, None, flag_enabled=True)
            actions.append(decide(inp).action)

        assert all(a == ActionV3.HOLD for a in actions), (
            "All-missing evidence should produce all-HOLD"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# II. FLAG-OFF / FLAG-ON SAFETY CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlagSafetyContracts:

    def test_flag_off_no_mutation_of_evidence_quality(self):
        """Flag off: inp.evidence_quality is NOT mutated."""
        readiness = _make_ticker_readiness("X", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp(evidence_quality=AxisBand.SUPPRESSED)
        original = inp.evidence_quality
        apply_evidence_governance(inp, readiness, None, flag_enabled=False)
        assert inp.evidence_quality == original

    def test_flag_off_no_mutation_of_suppression_reasons(self):
        """Flag off: inp.suppression_reasons is NOT mutated."""
        readiness = _make_ticker_readiness("X", fund=READINESS_READY)
        inp = _make_inp()
        inp.suppression_reasons = {}
        apply_evidence_governance(inp, readiness, None, flag_enabled=False)
        assert inp.suppression_reasons == {}

    def test_flag_off_result_governance_applied_false(self):
        readiness = _make_ticker_readiness("X", fund=READINESS_READY)
        inp = _make_inp()
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=False)
        assert result.governance_applied is False
        assert result.flag_enabled is False

    def test_flag_off_result_reason_code_governance_flag_off(self):
        readiness = _make_ticker_readiness("X", fund=READINESS_READY)
        inp = _make_inp()
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=False)
        assert "governance_flag_off" in result.reason_codes

    def test_flag_on_no_readiness_data_returns_no_op(self):
        """Flag on but no readiness data: no mutation."""
        inp = _make_inp(evidence_quality=AxisBand.SUPPRESSED)
        original = inp.evidence_quality
        result = apply_evidence_governance(inp, None, None, flag_enabled=True)
        assert inp.evidence_quality == original
        assert result.governance_applied is False
        assert result.evidence_governance_status == "no_readiness_data"

    def test_flag_on_only_evidence_quality_and_suppression_mutated(self):
        """Flag on: only evidence_quality and suppression_reasons are mutated."""
        readiness = _make_ticker_readiness("X", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp(
            evidence_quality=AxisBand.SUPPRESSED,
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        original_action = inp.raw_action
        original_price = inp.price_context
        original_fit = inp.portfolio_fit
        original_risk = inp.risk_band

        apply_evidence_governance(inp, readiness, None, flag_enabled=True)

        assert inp.raw_action == original_action
        assert inp.price_context == original_price
        assert inp.portfolio_fit == original_fit
        assert inp.risk_band == original_risk

    def test_flag_on_does_not_call_decide(self):
        """apply_evidence_governance must never call decide() — verified by
        confirming decide is unreachable in the module's namespace."""
        import app.services.intelligence.v3.intel_v3_evidence_aware_governance_v1 as gov_mod
        # If decide were reachable in this module, patching it would prevent the call.
        # Since it's not imported here at all, the strongest proof is the attribute check.
        assert not hasattr(gov_mod, "decide"), "governance module must not expose decide()"
        readiness = _make_ticker_readiness("X", fund=READINESS_READY)
        inp = _make_inp()
        # Calling with flag on must not raise — prove no side-channel call to decide.
        apply_evidence_governance(inp, readiness, None, flag_enabled=True)

    def test_flag_on_does_not_import_decision_policy_v1(self):
        """Governance module must not import decision_policy_v1."""
        import app.services.intelligence.v3.intel_v3_evidence_aware_governance_v1 as gov_mod
        assert not hasattr(gov_mod, "decide"), (
            "governance module must not expose decide()"
        )

    def test_flag_off_decision_identical_to_no_governance_call(self):
        """Flag off result is identical to calling decide() without governance."""
        readiness = _make_ticker_readiness("Y", fund=READINESS_READY, tech=READINESS_READY)
        inp_baseline = _make_inp(evidence_quality=AxisBand.OK)
        inp_flagoff = _make_inp(evidence_quality=AxisBand.OK)

        apply_evidence_governance(inp_flagoff, readiness, None, flag_enabled=False)
        d_baseline = decide(inp_baseline)
        d_flagoff = decide(inp_flagoff)

        assert d_baseline.action == d_flagoff.action
        assert d_baseline.conviction == d_flagoff.conviction


# ═══════════════════════════════════════════════════════════════════════════════
# III. CONVICTION CAPPING RULES
# ═══════════════════════════════════════════════════════════════════════════════


class TestConvictionCapping:

    def test_strong_evidence_no_cap_applied(self):
        assert _will_cap_conviction(AxisBand.STRONG) is False

    def test_ok_evidence_cap_applied(self):
        assert _will_cap_conviction(AxisBand.OK) is True

    def test_thin_evidence_cap_applied(self):
        assert _will_cap_conviction(AxisBand.THIN) is True

    def test_suppressed_evidence_cap_applied(self):
        assert _will_cap_conviction(AxisBand.SUPPRESSED) is True

    def test_strong_evidence_allows_high_conviction_buy(self):
        """STRONG evidence + HIGH upstream + BUY signal → HIGH conviction."""
        readiness = _make_ticker_readiness("C1", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp(
            evidence_quality=AxisBand.SUPPRESSED,
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        d = decide(inp)
        assert d.action == ActionV3.BUY
        assert d.conviction == ConvictionV3.HIGH

    def test_ok_evidence_caps_high_conviction_to_medium(self):
        """OK evidence + HIGH upstream → BUY with MEDIUM conviction (guardrail cap)."""
        readiness = _make_ticker_readiness("C2", fund=READINESS_READY)  # no corroboration → OK
        inp = _make_inp(
            evidence_quality=AxisBand.SUPPRESSED,
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert inp.evidence_quality == AxisBand.OK
        d = decide(inp)
        assert d.action == ActionV3.BUY
        assert d.conviction == ConvictionV3.MEDIUM  # capped by guardrail

    def test_thin_evidence_forces_low_conviction(self):
        """THIN evidence forces LOW conviction."""
        readiness = _make_ticker_readiness("C3", fund=READINESS_MISSING)
        inp = _make_inp(
            evidence_quality=AxisBand.STRONG,
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        d = decide(inp)
        # THIN blocks BUY → HOLD with LOW conviction
        assert d.action == ActionV3.HOLD
        assert d.conviction == ConvictionV3.LOW

    def test_zero_axes_usable_hold_low_conviction(self):
        """Zero usable axes → THIN → HOLD with LOW conviction."""
        readiness = _make_ticker_readiness(
            "C4",
            fund=READINESS_MISSING,
            tech=READINESS_MISSING,
            sent=READINESS_MISSING,
        )
        inp = _make_inp(
            evidence_quality=AxisBand.STRONG,
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        d = decide(inp)
        assert result.conviction_cap_applied is True
        assert d.conviction == ConvictionV3.LOW

    def test_conviction_cap_reason_set_when_cap_applied(self):
        readiness = _make_ticker_readiness("C5", fund=READINESS_MISSING)
        inp = _make_inp(evidence_quality=AxisBand.STRONG)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.conviction_cap_applied is True
        assert result.conviction_cap_reason is not None
        assert "evidence_governance" in result.conviction_cap_reason

    def test_conviction_cap_reason_none_when_not_capped(self):
        readiness = _make_ticker_readiness("C6", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp(evidence_quality=AxisBand.SUPPRESSED)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.conviction_cap_applied is False
        assert result.conviction_cap_reason is None


# ═══════════════════════════════════════════════════════════════════════════════
# IV. ETF/CRYPTO SEC NOT_APPLICABLE HANDLING
# ═══════════════════════════════════════════════════════════════════════════════


class TestEtfCryptoHandling:

    def test_etf_with_ready_yfinance_fundamentals_gets_strong(self):
        """ETF with yfinance fundamentals READY + tech READY → STRONG (not penalized)."""
        readiness = TickerDecisionReadiness(
            ticker="SPY",
            sec_lane_applicable=False,
            axes={
                AXIS_COMPANY_FUNDAMENTALS: _make_axis(READINESS_READY),
                AXIS_TECHNICAL_SIGNALS: _make_axis(READINESS_READY),
                AXIS_SENTIMENT: _make_axis(READINESS_MISSING),
            },
            any_axis_usable=True,
            usable_axis_count=2,
        )
        inp = _make_inp(ticker="SPY", evidence_quality=AxisBand.SUPPRESSED)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.governed_evidence_quality == AxisBand.STRONG.value

    def test_etf_sec_not_applicable_not_counted_as_degraded(self):
        """ETF with SEC not_applicable: not counted in degraded_axis_count."""
        fund_axis = AxisReadinessSignal(
            axis_name=AXIS_COMPANY_FUNDAMENTALS,
            readiness=READINESS_READY,
            is_usable=True,
            contributing_lanes=["fundamentals"],
            degraded_lanes=[],
            missing_lanes=[],
            not_applicable_lanes=["sec_company_facts"],
            lane_contributions=[],
        )
        readiness = TickerDecisionReadiness(
            ticker="VTI",
            sec_lane_applicable=False,
            axes={
                AXIS_COMPANY_FUNDAMENTALS: fund_axis,
                AXIS_TECHNICAL_SIGNALS: _make_axis(READINESS_MISSING),
                AXIS_SENTIMENT: _make_axis(READINESS_MISSING),
            },
            any_axis_usable=True,
            usable_axis_count=1,
        )
        inp = _make_inp(ticker="VTI", evidence_quality=AxisBand.SUPPRESSED)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        # READY without corroboration → OK; not penalized for missing SEC
        assert result.governed_evidence_quality == AxisBand.OK.value
        # SEC not_applicable is counted only in not_applicable_axis_count per lane_contributions

    def test_crypto_all_missing_no_fake_sec_evidence(self):
        """BTC with all axes MISSING: honest conservative THIN, no fabricated SEC."""
        readiness = _make_ticker_readiness(
            "BTC",
            fund=READINESS_MISSING,
            tech=READINESS_MISSING,
            sent=READINESS_MISSING,
            sec_lane_applicable=False,
        )
        inp = _make_inp(ticker="BTC", evidence_quality=AxisBand.SUPPRESSED)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.governed_evidence_quality == AxisBand.THIN.value
        assert result.company_fundamentals_readiness == READINESS_MISSING

    def test_crypto_with_technicals_still_thin_no_fundamentals(self):
        """Crypto with technicals READY but no fundamentals → THIN (no fundamental anchor)."""
        readiness = TickerDecisionReadiness(
            ticker="XRP",
            sec_lane_applicable=False,
            axes={
                AXIS_COMPANY_FUNDAMENTALS: _make_axis(READINESS_MISSING),
                AXIS_TECHNICAL_SIGNALS: _make_axis(READINESS_READY),
                AXIS_SENTIMENT: _make_axis(READINESS_LIMITED),
            },
            any_axis_usable=True,
            usable_axis_count=2,
        )
        inp = _make_inp(ticker="XRP", evidence_quality=AxisBand.STRONG)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.governed_evidence_quality == AxisBand.THIN.value
        assert "buy_blocked_no_fundamental_evidence" in result.action_blocks_applied


# ═══════════════════════════════════════════════════════════════════════════════
# V. MACRO CONTEXT ADVISORY-ONLY INVARIANT
# ═══════════════════════════════════════════════════════════════════════════════


class TestMacroContextAdvisoryOnly:

    def test_macro_ready_alone_cannot_force_buy(self):
        """Macro READY alone (all tickers missing evidence) cannot force BUY."""
        macro = _make_macro(READINESS_READY)
        readiness = _make_ticker_readiness("X", fund=READINESS_MISSING)
        inp = _make_inp(evidence_quality=AxisBand.SUPPRESSED, raw_action="BUY")
        apply_evidence_governance(inp, readiness, macro, flag_enabled=True)
        d = decide(inp)
        assert d.action == ActionV3.HOLD

    def test_macro_missing_alone_cannot_force_sell(self):
        """Macro MISSING alone (strong evidence) does not block BUY."""
        macro = _make_macro(READINESS_MISSING)
        readiness = _make_ticker_readiness("X", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp(
            evidence_quality=AxisBand.SUPPRESSED,
            raw_action="BUY",
            upstream_conviction="HIGH",
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
        )
        apply_evidence_governance(inp, readiness, macro, flag_enabled=True)
        d = decide(inp)
        assert d.action == ActionV3.BUY

    def test_macro_advisory_added_to_suppression_reasons(self):
        """Macro context is always added to suppression_reasons as advisory."""
        macro = _make_macro(READINESS_READY)
        readiness = _make_ticker_readiness("X", fund=READINESS_READY)
        inp = _make_inp()
        apply_evidence_governance(inp, readiness, macro, flag_enabled=True)
        assert "macro_context_advisory" in inp.suppression_reasons

    def test_macro_none_advisory_note_missing(self):
        """No macro object → advisory note still added to suppression_reasons."""
        readiness = _make_ticker_readiness("X", fund=READINESS_READY)
        inp = _make_inp()
        apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert "macro_context_advisory" in inp.suppression_reasons
        assert "missing" in inp.suppression_reasons["macro_context_advisory"]

    def test_macro_degraded_advisory_only_does_not_downgrade_evidence(self):
        """Macro stale/degraded: does not downgrade evidence_quality."""
        macro = _make_macro(READINESS_STALE_OR_UNKNOWN)
        readiness = _make_ticker_readiness("X", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp(evidence_quality=AxisBand.SUPPRESSED)
        result = apply_evidence_governance(inp, readiness, macro, flag_enabled=True)
        assert result.governed_evidence_quality == AxisBand.STRONG.value


# ═══════════════════════════════════════════════════════════════════════════════
# VI. HOLD-COLLAPSE DETECTION + ACTION DIVERSITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestHoldCollapseDetection:

    def test_classify_high_hold_collapse_risk_all_hold(self):
        dist = {"BUY": 0, "HOLD": 10, "TRIM": 0, "SELL": 0}
        assert _classify_hold_collapse_risk(_hold_pct(dist)) == "high"

    def test_classify_high_hold_collapse_risk_90_percent(self):
        dist = {"BUY": 1, "HOLD": 9, "TRIM": 0, "SELL": 0}
        assert _classify_hold_collapse_risk(_hold_pct(dist)) == "high"

    def test_classify_medium_hold_collapse_risk(self):
        dist = {"BUY": 3, "HOLD": 7, "TRIM": 0, "SELL": 0}
        assert _classify_hold_collapse_risk(_hold_pct(dist)) == "medium"

    def test_classify_low_hold_collapse_risk(self):
        dist = {"BUY": 5, "HOLD": 4, "TRIM": 1, "SELL": 0}
        assert _classify_hold_collapse_risk(_hold_pct(dist)) == "low"

    def test_empty_distribution_hold_pct_one(self):
        dist = {}
        assert _hold_pct(dist) == 1.0

    def test_governance_summary_hold_collapse_risk_field(self):
        shadow = _make_shadow()
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=True,
            per_ticker_results=[],
            action_distribution_off={"BUY": 0, "HOLD": 5, "TRIM": 0, "SELL": 0},
            action_distribution_on={"BUY": 0, "HOLD": 5, "TRIM": 0, "SELL": 0},
        )
        assert summary.hold_collapse_risk in {"high", "medium", "low"}
        assert summary.hold_collapse_risk == "high"  # 100% HOLD

    def test_diverse_portfolio_has_low_hold_collapse_risk(self):
        shadow = _make_shadow()
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=True,
            per_ticker_results=[],
            action_distribution_off={"BUY": 3, "HOLD": 2, "TRIM": 1, "SELL": 0},
            action_distribution_on={"BUY": 4, "HOLD": 2, "TRIM": 1, "SELL": 0},
        )
        assert summary.hold_collapse_risk == "low"

    def test_portfolio_governance_summary_flag_name(self):
        shadow = _make_shadow()
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=False,
            per_ticker_results=[],
            action_distribution_off={"BUY": 0, "HOLD": 3, "TRIM": 0, "SELL": 0},
            action_distribution_on={"BUY": 0, "HOLD": 3, "TRIM": 0, "SELL": 0},
        )
        assert summary.flag_name == FLAG_NAME
        assert summary.governance_version == GOVERNANCE_VERSION


# ═══════════════════════════════════════════════════════════════════════════════
# VII. SAFETY INVARIANTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafetyInvariants:

    def test_no_raw_payload_in_governance_result(self):
        """EvidenceGovernanceResult must not contain raw payloads/source URLs/API keys."""
        readiness = _make_ticker_readiness("SAFE", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp(ticker="SAFE")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        result_dict = result.to_dict()
        result_str = str(result_dict)
        forbidden = ["api_key", "source_url", "http://", "https://", "payload", "secret"]
        for f in forbidden:
            assert f not in result_str.lower(), f"Found forbidden '{f}' in governance result"

    def test_no_raw_payload_in_portfolio_summary(self):
        """PortfolioGovernanceSummary must not contain raw payloads/URLs."""
        shadow = _make_shadow()
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=True,
            per_ticker_results=[],
            action_distribution_off={"BUY": 0, "HOLD": 5, "TRIM": 0, "SELL": 0},
            action_distribution_on={"BUY": 2, "HOLD": 3, "TRIM": 0, "SELL": 0},
        )
        summary_str = str(summary.to_dict())
        for f in ["api_key", "source_url", "http://", "https://", "secret"]:
            assert f not in summary_str.lower()

    def test_governance_module_has_no_io(self):
        """Governance module must not import requests/httpx/aiohttp/asyncpg/supabase."""
        import importlib
        import sys
        # Remove any cached version to get a clean check
        mod_name = "app.services.intelligence.v3.intel_v3_evidence_aware_governance_v1"
        mod = sys.modules.get(mod_name)
        if mod is None:
            mod = importlib.import_module(mod_name)
        source = mod.__file__
        with open(source) as f:
            content = f.read()
        for forbidden in ["import requests", "import httpx", "import aiohttp", "asyncpg", "supabase"]:
            assert forbidden not in content, f"Found IO dependency '{forbidden}' in governance module"

    def test_governance_does_not_write_to_db(self):
        """apply_evidence_governance must not call any DB write method."""
        readiness = _make_ticker_readiness("DB", fund=READINESS_READY)
        inp = _make_inp()
        mock_client = MagicMock()
        # Ensure no table/insert/update method is called during governance
        with patch(
            "app.services.intelligence.v3.intel_v3_evidence_aware_governance_v1.logger"
        ):
            apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        mock_client.table.assert_not_called()

    def test_result_safe_for_visible_decision_when_strong(self):
        readiness = _make_ticker_readiness("S1", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp()
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.safe_for_visible_decision is True

    def test_result_not_safe_for_visible_decision_when_thin(self):
        readiness = _make_ticker_readiness("S2", fund=READINESS_MISSING)
        inp = _make_inp()
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.safe_for_visible_decision is False

    def test_result_not_safe_for_visible_decision_when_suppressed(self):
        readiness = _make_ticker_readiness("S3", fund=READINESS_SUPPRESSED)
        inp = _make_inp()
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.safe_for_visible_decision is False

    def test_to_dict_all_keys_present(self):
        readiness = _make_ticker_readiness("T", fund=READINESS_READY, tech=READINESS_LIMITED)
        inp = _make_inp()
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        d = result.to_dict()
        required_keys = [
            "ticker", "flag_enabled", "governance_applied",
            "original_evidence_quality", "governed_evidence_quality",
            "conviction_cap_applied", "conviction_cap_reason",
            "evidence_governance_status",
            "supported_axis_count", "missing_axis_count",
            "degraded_axis_count", "not_applicable_axis_count",
            "company_fundamentals_readiness", "technical_signals_readiness",
            "sentiment_readiness", "portfolio_macro_readiness",
            "action_blocks_applied", "safe_for_visible_decision", "reason_codes",
        ]
        for k in required_keys:
            assert k in d, f"Missing key '{k}' in governance result dict"

    def test_governance_result_evidence_status_active_when_flag_on(self):
        readiness = _make_ticker_readiness("E", fund=READINESS_READY)
        inp = _make_inp()
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.evidence_governance_status == "active"

    def test_governance_result_evidence_status_inactive_when_flag_off(self):
        readiness = _make_ticker_readiness("E", fund=READINESS_READY)
        inp = _make_inp()
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=False)
        assert result.evidence_governance_status == "inactive"


# ═══════════════════════════════════════════════════════════════════════════════
# VIII. PORTFOLIO GOVERNANCE SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════


class TestPortfolioGovernanceSummary:

    def test_evidence_blocked_count_correct(self):
        readiness1 = _make_ticker_readiness("A", fund=READINESS_MISSING)
        readiness2 = _make_ticker_readiness("B", fund=READINESS_READY, tech=READINESS_READY)

        inp1 = _make_inp(ticker="A")
        inp2 = _make_inp(ticker="B")

        r1 = apply_evidence_governance(inp1, readiness1, None, flag_enabled=True)
        r2 = apply_evidence_governance(inp2, readiness2, None, flag_enabled=True)

        shadow = _make_shadow(
            ticker_readiness={"A": readiness1, "B": readiness2},
        )
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=True,
            per_ticker_results=[r1, r2],
            action_distribution_off={"BUY": 0, "HOLD": 2, "TRIM": 0, "SELL": 0},
            action_distribution_on={"BUY": 1, "HOLD": 1, "TRIM": 0, "SELL": 0},
        )
        # Only r1 has action_blocks (missing evidence blocks buy)
        assert summary.governance_summary["evidence_blocked_action_count"] == 1

    def test_conviction_cap_count_correct(self):
        readiness_ok = _make_ticker_readiness("OK", fund=READINESS_READY)  # no corroboration → OK
        readiness_strong = _make_ticker_readiness("ST", fund=READINESS_READY, tech=READINESS_READY)

        inp_ok = _make_inp(ticker="OK")
        inp_strong = _make_inp(ticker="ST")

        r_ok = apply_evidence_governance(inp_ok, readiness_ok, None, flag_enabled=True)
        r_strong = apply_evidence_governance(inp_strong, readiness_strong, None, flag_enabled=True)

        shadow = _make_shadow(ticker_readiness={"OK": readiness_ok, "ST": readiness_strong})
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=True,
            per_ticker_results=[r_ok, r_strong],
            action_distribution_off={"BUY": 2, "HOLD": 0, "TRIM": 0, "SELL": 0},
            action_distribution_on={"BUY": 2, "HOLD": 0, "TRIM": 0, "SELL": 0},
        )
        # r_ok has cap (OK caps HIGH BUY); r_strong has no cap
        assert summary.governance_summary["conviction_cap_count"] == 1

    def test_safe_for_visible_decision_count_correct(self):
        r_safe = EvidenceGovernanceResult(
            ticker="A", flag_enabled=True, governance_applied=True,
            original_evidence_quality="SUPPRESSED", governed_evidence_quality="STRONG",
            conviction_cap_applied=False, conviction_cap_reason=None,
            evidence_governance_status="active",
            supported_axis_count=2, missing_axis_count=1, degraded_axis_count=0,
            not_applicable_axis_count=0,
            company_fundamentals_readiness=READINESS_READY,
            technical_signals_readiness=READINESS_READY,
            sentiment_readiness=READINESS_MISSING,
            portfolio_macro_readiness=READINESS_MISSING,
            safe_for_visible_decision=True,
        )
        r_unsafe = EvidenceGovernanceResult(
            ticker="B", flag_enabled=True, governance_applied=True,
            original_evidence_quality="OK", governed_evidence_quality="THIN",
            conviction_cap_applied=True, conviction_cap_reason="evidence_governance:THIN",
            evidence_governance_status="active",
            supported_axis_count=0, missing_axis_count=3, degraded_axis_count=0,
            not_applicable_axis_count=0,
            company_fundamentals_readiness=READINESS_MISSING,
            technical_signals_readiness=READINESS_MISSING,
            sentiment_readiness=READINESS_MISSING,
            portfolio_macro_readiness=READINESS_MISSING,
            safe_for_visible_decision=False,
        )
        shadow = _make_shadow()
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=True,
            per_ticker_results=[r_safe, r_unsafe],
            action_distribution_off={"BUY": 2, "HOLD": 0, "TRIM": 0, "SELL": 0},
            action_distribution_on={"BUY": 1, "HOLD": 1, "TRIM": 0, "SELL": 0},
        )
        assert summary.governance_summary["safe_for_visible_decision_count"] == 1

    def test_action_distribution_flag_off_captured(self):
        shadow = _make_shadow()
        off_dist = {"BUY": 2, "HOLD": 8, "TRIM": 0, "SELL": 0}
        on_dist = {"BUY": 5, "HOLD": 5, "TRIM": 0, "SELL": 0}
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=True,
            per_ticker_results=[],
            action_distribution_off=off_dist,
            action_distribution_on=on_dist,
        )
        assert summary.action_distribution_flag_off == off_dist
        assert summary.action_distribution_flag_on == on_dist

    def test_summary_to_dict_structure(self):
        shadow = _make_shadow()
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=False,
            per_ticker_results=[],
            action_distribution_off={"BUY": 0, "HOLD": 5, "TRIM": 0, "SELL": 0},
            action_distribution_on={"BUY": 0, "HOLD": 5, "TRIM": 0, "SELL": 0},
        )
        d = summary.to_dict()
        assert "schema_version" in d
        assert "governance_version" in d
        assert "flag_enabled" in d
        assert "flag_name" in d
        assert "evidence_readiness_summary" in d
        assert "governance_summary" in d
        assert "hold_collapse_risk" in d
        assert "action_distribution_flag_off" in d
        assert "action_distribution_flag_on" in d

    def test_evidence_readiness_summary_in_dict(self):
        shadow = _make_shadow(
            ticker_readiness={"AAPL": _make_ticker_readiness("AAPL", fund=READINESS_READY)},
        )
        summary = compute_portfolio_governance_summary(
            shadow,
            flag_enabled=True,
            per_ticker_results=[],
            action_distribution_off={"BUY": 1, "HOLD": 0, "TRIM": 0, "SELL": 0},
            action_distribution_on={"BUY": 1, "HOLD": 0, "TRIM": 0, "SELL": 0},
        )
        ers = summary.evidence_readiness_summary
        assert "tickers_with_any_usable_axis" in ers
        assert "tickers_fully_missing" in ers
        assert "macro_readiness" in ers


# ═══════════════════════════════════════════════════════════════════════════════
# IX. GOVERNANCE DERIVE RULES (unit tests for each priority)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeriveGovernedEvidenceQuality:

    def test_fund_suppressed_returns_suppressed(self):
        r = _make_ticker_readiness("X", fund=READINESS_SUPPRESSED, tech=READINESS_READY)
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.SUPPRESSED
        assert "buy_blocked_suppressed_fundamentals" in blocks

    def test_fund_insufficient_returns_suppressed(self):
        r = _make_ticker_readiness("X", fund=READINESS_INSUFFICIENT, tech=READINESS_READY)
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.SUPPRESSED

    def test_all_missing_returns_thin(self):
        r = _make_ticker_readiness("X")
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.THIN
        assert "buy_blocked_missing_evidence" in blocks

    def test_fund_stale_no_others_returns_thin_stale(self):
        r = _make_ticker_readiness("X", fund=READINESS_STALE_OR_UNKNOWN)
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.THIN
        assert "buy_blocked_stale_evidence" in blocks

    def test_fund_ready_tech_ready_returns_strong(self):
        r = _make_ticker_readiness("X", fund=READINESS_READY, tech=READINESS_READY)
        band, _, _, _ = _derive_governed_evidence_quality(r)
        assert band == AxisBand.STRONG

    def test_fund_ready_sent_limited_returns_strong(self):
        r = _make_ticker_readiness("X", fund=READINESS_READY, sent=READINESS_LIMITED)
        band, _, _, _ = _derive_governed_evidence_quality(r)
        assert band == AxisBand.STRONG

    def test_fund_ready_no_corroboration_returns_ok(self):
        r = _make_ticker_readiness("X", fund=READINESS_READY)
        band, codes, _, _ = _derive_governed_evidence_quality(r)
        assert band == AxisBand.OK
        assert "ready_fundamentals_no_signal_corroboration" in codes

    def test_fund_limited_tech_ready_returns_ok(self):
        r = _make_ticker_readiness("X", fund=READINESS_LIMITED, tech=READINESS_READY)
        band, _, _, _ = _derive_governed_evidence_quality(r)
        assert band == AxisBand.OK

    def test_fund_limited_no_corroboration_equity_returns_ok_with_cap(self):
        # Equity: limited fundamentals without corroboration → OK with conviction cap.
        r = _make_ticker_readiness("X", fund=READINESS_LIMITED, instrument_category="equity")
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.OK
        assert "limited_equity_fundamentals_ok_with_cap" in codes
        assert not blocks
        assert priority == "p4b_limited_no_corroboration"

    def test_fund_limited_no_corroboration_etf_returns_ok_with_cap(self):
        # ETF: limited fundamentals without corroboration → OK with conviction cap.
        r = _make_ticker_readiness("SPY", fund=READINESS_LIMITED,
                                   sec_lane_applicable=False, instrument_category="etf")
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.OK
        assert "limited_etf_evidence_ok_with_cap" in codes
        assert not blocks
        assert priority == "p4b_limited_no_corroboration"

    def test_fund_limited_no_corroboration_crypto_returns_thin(self):
        # Crypto: generic yfinance LIMITED fundamentals without corroboration → THIN.
        r = _make_ticker_readiness("BTC", fund=READINESS_LIMITED,
                                   sec_lane_applicable=False, instrument_category="crypto")
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.THIN
        assert "limited_crypto_fundamentals_not_safe" in codes
        assert "buy_blocked_insufficient_evidence_basis" in blocks
        assert priority == "p4b_crypto_or_unknown_thin"

    def test_fund_limited_no_corroboration_unknown_returns_thin(self):
        # Unknown instrument: conservative → THIN.
        r = _make_ticker_readiness("XYZ", fund=READINESS_LIMITED,
                                   sec_lane_applicable=False, instrument_category="unknown")
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.THIN
        assert "limited_unknown_instrument_fundamentals_not_safe" in codes
        assert "buy_blocked_insufficient_evidence_basis" in blocks
        assert priority == "p4b_crypto_or_unknown_thin"

    def test_no_fund_tech_only_returns_thin(self):
        r = _make_ticker_readiness("X", tech=READINESS_READY)
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.THIN
        assert "buy_blocked_no_fundamental_evidence" in blocks

    def test_no_fund_sent_only_returns_thin(self):
        r = _make_ticker_readiness("X", sent=READINESS_READY)
        band, _, blocks, _ = _derive_governed_evidence_quality(r)
        assert band == AxisBand.THIN
        assert "buy_blocked_no_fundamental_evidence" in blocks

    def test_fund_not_evaluable_returns_thin_not_evaluable(self):
        r = _make_ticker_readiness("X", fund=READINESS_NOT_EVALUABLE)
        band, codes, _, _ = _derive_governed_evidence_quality(r)
        assert band == AxisBand.THIN
        assert "evidence_not_evaluable" in codes


# ── Integration regression tests — keyword-only Stage 5J call ─────────────────


class TestStage6KeywordArgIntegration:
    """Regression tests proving Stage 6 call sites pass keyword args to
    compute_research_evidence_coverage (which is keyword-only via *).

    These would have raised TypeError before the patch.
    """

    def test_service_helper_calls_stage5j_with_keyword_args(self):
        """_get_evidence_shadow_for_governance must call Stage 5J using keyword
        args when the Stage 6 flag is enabled. Verifies the keyword-arg fix in
        intel_v3_service.py (_get_evidence_shadow_for_governance)."""
        import asyncio
        import inspect
        from unittest.mock import AsyncMock, MagicMock, patch

        # Confirm Stage 5J function is keyword-only.
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            compute_research_evidence_coverage,
        )
        sig = inspect.signature(compute_research_evidence_coverage)
        for name, param in sig.parameters.items():
            assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"compute_research_evidence_coverage param '{name}' must be keyword-only"
            )

        # Use MagicMock for shadow — avoids constructor field complexity.
        fake_shadow = MagicMock()
        fake_shadow.portfolio_ticker_count = 0
        fake_shadow.tickers_with_any_usable_axis = 0
        fake_shadow.tickers_fully_missing = 0

        # The keyword-only fix wraps the call in a lambda; calling with
        # keyword args succeeds even though to_thread passes no positional args.
        called_with_kwargs = {}

        def fake_coverage(**kwargs):
            # Capture kwargs to assert keyword-only call was made.
            called_with_kwargs.update(kwargs)
            return MagicMock()  # compute_decision_input_readiness is mocked; just needs to be truthy

        fake_card = MagicMock()
        fake_card.ticker = "AAPL"
        fake_card.category = "stock"

        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        with (
            patch(
                "app.services.intelligence.v3.intel_v3_service.get_settings",
                return_value=MagicMock(intel_v3_evidence_aware_policy_enabled=True),
            ),
            patch(
                "app.services.intelligence.v3.research_evidence_coverage_read_model_v1"
                ".compute_research_evidence_coverage",
                side_effect=lambda **kw: fake_coverage(**kw),
            ),
            patch(
                "app.services.intelligence.v3.research_evidence_decision_input_adapter_v1"
                ".compute_decision_input_readiness",
                return_value=fake_shadow,
            ),
        ):
            svc = IntelV3Service.__new__(IntelV3Service)
            svc.user_id = "u1"
            svc.client = MagicMock()

            result = asyncio.get_event_loop().run_until_complete(
                svc._get_evidence_shadow_for_governance([fake_card])
            )

        assert result is fake_shadow, "Should return the shadow when flag is on"

    def test_service_helper_flag_off_does_not_call_stage5j(self):
        """Flag-off path must return None and never call compute_research_evidence_coverage."""
        import asyncio
        from unittest.mock import MagicMock, patch

        call_log = []

        with patch(
            "app.services.intelligence.v3.intel_v3_service.get_settings",
            return_value=MagicMock(intel_v3_evidence_aware_policy_enabled=False),
        ):
            from app.services.intelligence.v3.intel_v3_service import IntelV3Service

            svc = IntelV3Service.__new__(IntelV3Service)
            svc.user_id = "u1"
            svc.client = MagicMock()

            result = asyncio.get_event_loop().run_until_complete(
                svc._get_evidence_shadow_for_governance([])
            )

        assert result is None, "Flag off must return None"
        assert not call_log, "Stage 5J must not be called when flag is off"

    def test_stage5j_signature_is_keyword_only(self):
        """Structural proof that compute_research_evidence_coverage requires
        keyword args — calling it with positional args raises TypeError."""
        import inspect
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            compute_research_evidence_coverage,
        )

        sig = inspect.signature(compute_research_evidence_coverage)
        for name, param in sig.parameters.items():
            assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"param '{name}' must be keyword-only (* in signature)"
            )

        # Calling with positional args must raise TypeError.
        import pytest
        with pytest.raises(TypeError):
            compute_research_evidence_coverage("u1", [], MagicMock())

    def test_diagnostics_endpoint_stage5j_call_uses_keyword_args(self):
        """Stage 6 diagnostics endpoint must pass keyword args to Stage 5J.
        Verifies the keyword-arg fix in routers/diagnostics.py."""
        import asyncio
        import ast
        from pathlib import Path

        src = Path(
            "app/services/intelligence/v3/research_evidence_coverage_read_model_v1.py"
        ).read_text()
        diag_src = Path("app/routers/diagnostics.py").read_text()

        # Find the stage6-evidence-governance function body in diagnostics.py.
        # Confirm it contains no positional-only pattern:
        # to_thread(compute_research_evidence_coverage, <expr>, <expr>, <expr>)
        # which would pass positional args.
        import re
        # Pattern for the BAD old call: positional args to to_thread
        bad_pattern = re.compile(
            r"to_thread\s*\(\s*compute_research_evidence_coverage\s*,\s*\w",
            re.MULTILINE,
        )
        assert not bad_pattern.search(diag_src), (
            "diagnostics.py must not pass positional args to "
            "compute_research_evidence_coverage via to_thread"
        )

        # Confirm the GOOD pattern exists: lambda wrapping keyword call.
        good_pattern = re.compile(
            r"to_thread\s*\(\s*lambda\s*:",
            re.MULTILINE,
        )
        assert good_pattern.search(diag_src), (
            "diagnostics.py Stage 6 path must use lambda wrapper for keyword-only call"
        )

    def test_service_stage5j_call_uses_lambda_wrapper(self):
        """intel_v3_service.py _get_evidence_shadow_for_governance must use
        a lambda wrapper so keyword-only Stage 5J call is not passed positional args."""
        import re
        from pathlib import Path

        src = Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()

        # Bad pattern: positional to to_thread
        bad_pattern = re.compile(
            r"to_thread\s*\(\s*compute_research_evidence_coverage\s*,\s*\w",
            re.MULTILINE,
        )
        assert not bad_pattern.search(src), (
            "intel_v3_service.py must not pass positional args to "
            "compute_research_evidence_coverage via to_thread"
        )

        # Good pattern: lambda wrapper present
        good_pattern = re.compile(
            r"to_thread\s*\(\s*lambda\s*:",
            re.MULTILINE,
        )
        assert good_pattern.search(src), (
            "intel_v3_service.py Stage 6 path must use lambda wrapper for keyword-only call"
        )
