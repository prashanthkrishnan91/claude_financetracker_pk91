"""Stage 6 — Governance Calibration Tests (2026-05-19).

Covers the specific calibration changes made to address the production
hold-collapse issue where flag-on collapsed all BUYs to HOLD even when
company fundamentals/SEC evidence was usable (33/34 tickers).

Root causes fixed:
  1. technical_signal SUPPRESSED_UNKNOWN_SOURCE: yfinance price history was
     classified as source_kind="other" → AuthorityLevel.UNKNOWN → suppressed.
     Fix: narrow yfinance-price-history provider-aware override in
     source_credibility_registry_v1 maps it to VENDOR_DERIVED.

  2. Stage 6 Priority 4b collapse: fund=LIMITED + no tech/sent → THIN → all
     BUYs blocked. Fix: Priority 4b now returns OK (conviction capped to
     MEDIUM by existing guardrail) instead of THIN.

  3. sentiment SUPPRESSED_INCOMPLETE: editorial-only sources → THIN
     completeness (by design). No change — keeps honest suppression.

Tests verify:
  A. yfinance price history source credibility override (VENDOR_DERIVED, not UNKNOWN).
  B. Technicals can now reach USABLE_WITH_LIMITATIONS after the override.
  C. Calibration: fund_limited + no corroboration → OK (not THIN) + BUY allowed.
  D. Flag-off visible behavior still identical to no-governance call.
  E. Flag-on production scenario: fundamentals usable (33 tickers) → nonzero safe count.
  F. Suppressed fundamentals still block BUY (regression guard).
  G. Missing fundamentals still block BUY (regression guard).
  H. ETF/crypto SEC not_applicable remains honest and not penalized.
  I. Macro cannot independently force BUY/SELL.
  J. No provider calls, no LLM calls, no DB writes.
  K. No raw payload/source URL/secret in diagnostics.
  L. Sentiment intentionally stays suppressed (honest thin coverage).
  M. New diagnostic fields present and correct.
  N. Priority applied strings correct.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

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
    apply_evidence_governance,
    _derive_governed_evidence_quality,
)
from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
    AXIS_COMPANY_FUNDAMENTALS,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL_SIGNALS,
    READINESS_LIMITED,
    READINESS_MISSING,
    READINESS_NOT_APPLICABLE,
    READINESS_READY,
    READINESS_SUPPRESSED,
    READINESS_STALE_OR_UNKNOWN,
    AxisReadinessSignal,
    TickerDecisionReadiness,
)
from app.services.intelligence.v3.decision_policy_v1 import decide
from app.services.intelligence.v3.source_credibility_registry_v1 import (
    AuthorityLevel,
    _matches_yfinance_price_history_source,
    _YFINANCE_PRICE_HISTORY_OVERRIDE,
    assess_artifact_sources,
)
from app.services.intelligence.research_workers.contracts import SourceRecord


# ── Minimal test helpers ──────────────────────────────────────────────────────


def _make_axis(readiness: str) -> AxisReadinessSignal:
    is_usable = readiness in {READINESS_READY, READINESS_LIMITED}
    contributing = ["lane"] if is_usable else []
    degraded = (
        ["lane"] if not is_usable and readiness not in {READINESS_MISSING, READINESS_NOT_APPLICABLE}
        else []
    )
    missing = ["lane"] if readiness == READINESS_MISSING else []
    not_applicable = ["lane"] if readiness == READINESS_NOT_APPLICABLE else []
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


def _make_readiness(
    ticker: str,
    *,
    fund: str = READINESS_MISSING,
    tech: str = READINESS_MISSING,
    sent: str = READINESS_MISSING,
    sec_applicable: bool = True,
) -> TickerDecisionReadiness:
    axes = {
        AXIS_COMPANY_FUNDAMENTALS: _make_axis(fund),
        AXIS_TECHNICAL_SIGNALS: _make_axis(tech),
        AXIS_SENTIMENT: _make_axis(sent),
    }
    usable = sum(1 for a in axes.values() if a.is_usable)
    return TickerDecisionReadiness(
        ticker=ticker,
        sec_lane_applicable=sec_applicable,
        axes=axes,
        any_axis_usable=usable > 0,
        usable_axis_count=usable,
    )


def _make_inp(
    ticker: str = "AAPL",
    *,
    evidence_quality: AxisBand = AxisBand.SUPPRESSED,
    raw_action: str = "BUY",
    upstream_conviction: str = "HIGH",
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


def _make_yfinance_history_source() -> SourceRecord:
    return SourceRecord(
        source_kind="other",
        provider_name="yfinance",
        provider_version="yfinance_price_history_sync_v1",
        source_published_at="2026-05-19T00:00:00+00:00",
        fetched_at="2026-05-19T00:00:00+00:00",
        section_reference="yfinance.Ticker.history(period=3mo)",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A. YFINANCE PRICE HISTORY SOURCE CREDIBILITY OVERRIDE
# ═══════════════════════════════════════════════════════════════════════════════


class TestYfinancePriceHistorySourceOverride:
    """yfinance price history should be VENDOR_DERIVED, not UNKNOWN."""

    def test_matches_yfinance_history_source(self):
        assert _matches_yfinance_price_history_source(
            source_kind="other",
            provider_name="yfinance",
            section_reference="yfinance.Ticker.history(period=3mo)",
        ) is True

    def test_does_not_match_different_provider(self):
        assert _matches_yfinance_price_history_source(
            source_kind="other",
            provider_name="bloomberg",
            section_reference="history",
        ) is False

    def test_does_not_match_wrong_source_kind(self):
        assert _matches_yfinance_price_history_source(
            source_kind="vendor_fundamentals",
            provider_name="yfinance",
            section_reference="yfinance.Ticker.history(period=3mo)",
        ) is False

    def test_does_not_match_no_history_in_reference(self):
        assert _matches_yfinance_price_history_source(
            source_kind="other",
            provider_name="yfinance",
            section_reference="yfinance.Ticker.info",
        ) is False

    def test_does_not_match_missing_section_reference(self):
        assert _matches_yfinance_price_history_source(
            source_kind="other",
            provider_name="yfinance",
            section_reference=None,
        ) is False

    def test_override_authority_is_vendor_derived(self):
        assert _YFINANCE_PRICE_HISTORY_OVERRIDE.authority_level == AuthorityLevel.VENDOR_DERIVED

    def test_override_does_not_claim_primary_authority(self):
        assert _YFINANCE_PRICE_HISTORY_OVERRIDE.authority_level != AuthorityLevel.PRIMARY_AUTHORITY

    def test_override_does_not_claim_unknown(self):
        assert _YFINANCE_PRICE_HISTORY_OVERRIDE.authority_level != AuthorityLevel.UNKNOWN

    def test_assess_yfinance_history_source_is_vendor_derived(self):
        src = _make_yfinance_history_source()
        result = assess_artifact_sources([src])
        assert result.strongest_authority_level == AuthorityLevel.VENDOR_DERIVED.value
        assert result.is_insufficient is False

    def test_assess_yfinance_history_override_id_set(self):
        src = _make_yfinance_history_source()
        result = assess_artifact_sources([src])
        pa = result.per_source_assessments[0]
        assert pa["provider_aware_override_applied"] is True
        assert pa["provider_aware_override_id"] == "yfinance_price_history_vendor_v1"

    def test_generic_other_source_without_yfinance_stays_unknown(self):
        src = SourceRecord(source_kind="other", provider_name="unknown_vendor")
        result = assess_artifact_sources([src])
        assert result.is_insufficient is True
        assert result.strongest_authority_level == AuthorityLevel.UNKNOWN.value

    def test_yfinance_info_source_not_overridden(self):
        """yfinance.Ticker.info (fundamentals) uses vendor_fundamentals, not other."""
        src = SourceRecord(
            source_kind="vendor_fundamentals",
            provider_name="yfinance",
            section_reference="yfinance.Ticker.info",
        )
        result = assess_artifact_sources([src])
        assert result.per_source_assessments[0]["provider_aware_override_applied"] is False

    def test_override_limitation_does_not_claim_decision_authority(self):
        limitations = _YFINANCE_PRICE_HISTORY_OVERRIDE.limitations
        # Must not positively claim decision authority — check for affirmative phrases.
        # The limitations text is allowed to say "does not support Buy/Hold/Trim/Sell"
        # (disclaimer), but must not say "supports Buy" or "constitutes ... recommendation".
        positive_authority_phrases = [
            "supports buy",
            "supports sell",
            "supports hold",
            "supports trim",
            "constitutes a recommendation",
            "price target",
            "conviction: high",
        ]
        low = limitations.lower()
        for phrase in positive_authority_phrases:
            assert phrase not in low, f"Found positive authority claim '{phrase}' in yfinance override limitations"


# ═══════════════════════════════════════════════════════════════════════════════
# B. TECHNICALS COMPLETENESS WITH VENDOR_DERIVED OVERRIDE
# ═══════════════════════════════════════════════════════════════════════════════


class TestTechnicalsCompletenessAfterOverride:
    """After VENDOR_DERIVED override, technicals can reach PARTIAL completeness."""

    def test_vendor_derived_not_in_thin_cap_authority_levels(self):
        from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
            _THIN_CAP_AUTHORITY_LEVELS,
        )
        assert AuthorityLevel.VENDOR_DERIVED.value not in _THIN_CAP_AUTHORITY_LEVELS

    def test_editorial_still_in_thin_cap_authority_levels(self):
        from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
            _THIN_CAP_AUTHORITY_LEVELS,
        )
        assert AuthorityLevel.EDITORIAL_CONTEXT.value in _THIN_CAP_AUTHORITY_LEVELS

    def test_unknown_still_in_thin_cap_authority_levels(self):
        from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
            _THIN_CAP_AUTHORITY_LEVELS,
        )
        assert AuthorityLevel.UNKNOWN.value in _THIN_CAP_AUTHORITY_LEVELS

    def test_technicals_source_credibility_vendor_derived_is_not_insufficient(self):
        """After the override, yfinance technicals credibility is not insufficient."""
        src = _make_yfinance_history_source()
        credibility = assess_artifact_sources([src])
        assert not credibility.is_insufficient
        assert credibility.strongest_authority_level == AuthorityLevel.VENDOR_DERIVED.value


# ═══════════════════════════════════════════════════════════════════════════════
# C. PRIORITY 4B CALIBRATION: fund_limited + no corroboration → OK (not THIN)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPriority4bCalibration:
    """Core calibration change: fund_limited alone → OK (BUY allowed with cap)."""

    def test_fund_limited_no_tech_no_sent_returns_ok(self):
        r = _make_readiness("X", fund=READINESS_LIMITED)
        band, codes, blocks, priority = _derive_governed_evidence_quality(r)
        assert band == AxisBand.OK
        assert "limited_fundamentals_no_corroboration_ok_with_cap" in codes
        assert not blocks
        assert priority == "p4b_limited_no_corroboration"

    def test_fund_limited_no_corroboration_allows_buy_with_medium_conviction(self):
        """Priority 4b: fund_limited → OK → BUY allowed, conviction capped to MEDIUM."""
        readiness = _make_readiness("AAPL", fund=READINESS_LIMITED)
        inp = _make_inp("AAPL", evidence_quality=AxisBand.SUPPRESSED)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)

        assert result.governed_evidence_quality == AxisBand.OK.value
        assert result.governance_priority_applied == "p4b_limited_no_corroboration"
        assert decision.action == ActionV3.BUY
        assert decision.conviction == ConvictionV3.MEDIUM  # OK caps HIGH to MEDIUM

    def test_fund_limited_no_corroboration_buy_is_not_high_conviction(self):
        """Priority 4b: conviction capped — never HIGH for OK evidence."""
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X", upstream_conviction="HIGH")
        apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        d = decide(inp)
        assert d.conviction != ConvictionV3.HIGH

    def test_fund_limited_no_corroboration_corroboration_gap_true(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.corroboration_gap is True

    def test_fund_limited_with_tech_corroboration_not_4b_path(self):
        """Priority 4a applies when corroboration exists (not 4b)."""
        readiness = _make_readiness("X", fund=READINESS_LIMITED, tech=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.governance_priority_applied == "p4a_limited_corroborated"
        assert result.corroboration_gap is False

    def test_fund_limited_with_sentiment_corroboration_not_4b_path(self):
        """Priority 4a with sentiment corroboration."""
        readiness = _make_readiness("X", fund=READINESS_LIMITED, sent=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.governance_priority_applied == "p4a_limited_corroborated"

    def test_fund_limited_sec_and_no_tech_allows_buy(self):
        """Equity with SEC (LIMITED) as sole company_fundamentals contributor: BUY allowed."""
        readiness = _make_readiness("MSFT", fund=READINESS_LIMITED, sec_applicable=True)
        inp = _make_inp("MSFT", evidence_quality=AxisBand.SUPPRESSED)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)
        assert decision.action == ActionV3.BUY
        assert result.governed_evidence_quality == AxisBand.OK.value


# ═══════════════════════════════════════════════════════════════════════════════
# D. FLAG-OFF UNCHANGED
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlagOffUnchanged:
    """Flag-off is a complete no-op — identical to baseline."""

    def test_flag_off_fund_limited_no_change(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X", evidence_quality=AxisBand.OK)
        original = inp.evidence_quality
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=False)
        assert inp.evidence_quality == original
        assert result.governance_applied is False

    def test_flag_off_decision_identical_to_no_governance(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp_baseline = _make_inp("X", evidence_quality=AxisBand.OK)
        inp_flagoff = _make_inp("X", evidence_quality=AxisBand.OK)
        apply_evidence_governance(inp_flagoff, readiness, None, flag_enabled=False)
        d_b = decide(inp_baseline)
        d_f = decide(inp_flagoff)
        assert d_b.action == d_f.action
        assert d_b.conviction == d_f.conviction

    def test_flag_off_suppression_reasons_unchanged(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X")
        inp.suppression_reasons = {}
        apply_evidence_governance(inp, readiness, None, flag_enabled=False)
        assert inp.suppression_reasons == {}


# ═══════════════════════════════════════════════════════════════════════════════
# E. PRODUCTION SCENARIO: 33/34 USABLE FUNDAMENTALS → NONZERO SAFE COUNT
# ═══════════════════════════════════════════════════════════════════════════════


class TestProductionScenarioCalibration:
    """Simulate the production distribution: 33 tickers with usable fundamentals."""

    def test_fund_limited_all_tickers_not_all_hold_after_calibration(self):
        """33 tickers with LIMITED fundamentals, no tech/sent corroboration.

        Before calibration: all 33 would be THIN → HOLD.
        After calibration: all 33 should be OK → BUY (when signal says BUY).
        """
        tickers = [f"T{i}" for i in range(33)]
        buy_count = 0
        safe_count = 0
        for ticker in tickers:
            readiness = _make_readiness(ticker, fund=READINESS_LIMITED)
            inp = _make_inp(
                ticker,
                evidence_quality=AxisBand.SUPPRESSED,
                raw_action="BUY",
                upstream_conviction="HIGH",
                portfolio_fit=FitBand.UNDERWEIGHT,
                price_context=PriceBand.FAIR,
                risk_band=RiskBand.LOW,
            )
            result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
            decision = decide(inp)
            if result.safe_for_visible_decision:
                safe_count += 1
            if decision.action == ActionV3.BUY:
                buy_count += 1

        assert safe_count == 33, f"Expected 33 safe decisions, got {safe_count}"
        assert buy_count == 33, f"Expected 33 BUYs, got {buy_count}"

    def test_one_fully_missing_ticker_stays_thin(self):
        """The 1 fully missing ticker (tickers_fully_missing=1) stays THIN."""
        readiness = _make_readiness(
            "MISSING_T",
            fund=READINESS_MISSING,
            tech=READINESS_MISSING,
            sent=READINESS_MISSING,
        )
        inp = _make_inp("MISSING_T", evidence_quality=AxisBand.OK)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)
        assert result.governed_evidence_quality == AxisBand.THIN.value
        assert decision.action == ActionV3.HOLD
        assert result.safe_for_visible_decision is False

    def test_safe_for_visible_decision_true_when_fund_limited_ok(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.safe_for_visible_decision is True

    def test_safe_for_visible_decision_false_when_all_missing(self):
        readiness = _make_readiness("X")
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.safe_for_visible_decision is False


# ═══════════════════════════════════════════════════════════════════════════════
# F. SUPPRESSED FUNDAMENTALS STILL BLOCK BUY (REGRESSION GUARD)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSuppressedFundamentalsStillBlock:

    def test_suppressed_fundamentals_returns_suppressed_band(self):
        readiness = _make_readiness("X", fund=READINESS_SUPPRESSED, tech=READINESS_READY)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.governed_evidence_quality == AxisBand.SUPPRESSED.value

    def test_suppressed_fundamentals_blocks_buy_hold_result(self):
        readiness = _make_readiness("X", fund=READINESS_SUPPRESSED, tech=READINESS_READY)
        inp = _make_inp("X", evidence_quality=AxisBand.STRONG, raw_action="BUY")
        apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        d = decide(inp)
        assert d.action == ActionV3.HOLD

    def test_suppressed_fundamentals_has_buy_blocked_in_action_blocks(self):
        readiness = _make_readiness("X", fund=READINESS_SUPPRESSED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert "buy_blocked_suppressed_fundamentals" in result.action_blocks_applied

    def test_suppressed_fundamentals_safe_for_visible_decision_false(self):
        readiness = _make_readiness("X", fund=READINESS_SUPPRESSED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.safe_for_visible_decision is False

    def test_p1_priority_applied_for_suppressed(self):
        readiness = _make_readiness("X", fund=READINESS_SUPPRESSED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.governance_priority_applied == "p1_suppressed_fundamentals"


# ═══════════════════════════════════════════════════════════════════════════════
# G. MISSING FUNDAMENTALS STILL BLOCK BUY (REGRESSION GUARD)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingFundamentalsStillBlock:

    def test_all_missing_returns_thin_not_ok(self):
        readiness = _make_readiness("X")
        band, codes, blocks, priority = _derive_governed_evidence_quality(readiness)
        assert band == AxisBand.THIN
        assert "buy_blocked_missing_evidence" in blocks

    def test_all_missing_hold_result(self):
        readiness = _make_readiness("X")
        inp = _make_inp("X", raw_action="BUY")
        apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        d = decide(inp)
        assert d.action == ActionV3.HOLD

    def test_no_fundamental_anchor_tech_only_thin(self):
        readiness = _make_readiness("X", tech=READINESS_READY, sent=READINESS_READY)
        band, codes, blocks, priority = _derive_governed_evidence_quality(readiness)
        assert band == AxisBand.THIN
        assert "buy_blocked_no_fundamental_evidence" in blocks
        assert priority == "p5_no_fundamental_anchor"

    def test_stale_fundamentals_no_others_thin(self):
        readiness = _make_readiness("X", fund=READINESS_STALE_OR_UNKNOWN)
        band, codes, blocks, priority = _derive_governed_evidence_quality(readiness)
        assert band == AxisBand.THIN
        assert "buy_blocked_stale_evidence" in blocks


# ═══════════════════════════════════════════════════════════════════════════════
# H. ETF/CRYPTO SEC NOT_APPLICABLE HONEST AND NOT PENALIZED
# ═══════════════════════════════════════════════════════════════════════════════


class TestEtfCryptoNotPenalized:

    def test_etf_fund_limited_no_sec_not_penalized(self):
        """ETF with fund=LIMITED + sec_applicable=False → Priority 4b → OK (not THIN)."""
        readiness = TickerDecisionReadiness(
            ticker="SPY",
            sec_lane_applicable=False,
            axes={
                AXIS_COMPANY_FUNDAMENTALS: _make_axis(READINESS_LIMITED),
                AXIS_TECHNICAL_SIGNALS: _make_axis(READINESS_MISSING),
                AXIS_SENTIMENT: _make_axis(READINESS_MISSING),
            },
            any_axis_usable=True,
            usable_axis_count=1,
        )
        band, codes, blocks, priority = _derive_governed_evidence_quality(readiness)
        assert band == AxisBand.OK  # not penalized for missing SEC
        assert not blocks

    def test_etf_fund_ready_no_sec_not_penalized(self):
        """ETF with fund=READY + no SEC → Priority 3b → OK."""
        readiness = TickerDecisionReadiness(
            ticker="QQQ",
            sec_lane_applicable=False,
            axes={
                AXIS_COMPANY_FUNDAMENTALS: _make_axis(READINESS_READY),
                AXIS_TECHNICAL_SIGNALS: _make_axis(READINESS_MISSING),
                AXIS_SENTIMENT: _make_axis(READINESS_MISSING),
            },
            any_axis_usable=True,
            usable_axis_count=1,
        )
        band, codes, blocks, priority = _derive_governed_evidence_quality(readiness)
        assert band == AxisBand.OK
        assert priority == "p3b_ready_no_corroboration"

    def test_crypto_all_missing_honest_thin(self):
        """Crypto with all axes MISSING → THIN (honest, no fabricated SEC)."""
        readiness = _make_readiness(
            "BTC", fund=READINESS_MISSING, tech=READINESS_MISSING,
            sent=READINESS_MISSING, sec_applicable=False,
        )
        band, codes, blocks, priority = _derive_governed_evidence_quality(readiness)
        assert band == AxisBand.THIN
        assert "buy_blocked_missing_evidence" in blocks


# ═══════════════════════════════════════════════════════════════════════════════
# I. MACRO CANNOT INDEPENDENTLY FORCE BUY/SELL
# ═══════════════════════════════════════════════════════════════════════════════


class TestMacroAdvisoryOnly:

    def test_macro_ready_with_missing_fundamentals_still_hold(self):
        macro = _make_axis(READINESS_READY)
        readiness = _make_readiness("X", fund=READINESS_MISSING)
        inp = _make_inp("X", raw_action="BUY")
        apply_evidence_governance(inp, readiness, macro, flag_enabled=True)
        d = decide(inp)
        assert d.action == ActionV3.HOLD

    def test_macro_missing_does_not_block_buy_with_good_fundamentals(self):
        macro = _make_axis(READINESS_MISSING)
        readiness = _make_readiness("X", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp("X", evidence_quality=AxisBand.SUPPRESSED)
        apply_evidence_governance(inp, readiness, macro, flag_enabled=True)
        d = decide(inp)
        assert d.action == ActionV3.BUY

    def test_macro_stale_does_not_downgrade_good_evidence(self):
        macro = _make_axis(READINESS_STALE_OR_UNKNOWN)
        readiness = _make_readiness("X", fund=READINESS_READY, tech=READINESS_READY)
        inp = _make_inp("X", evidence_quality=AxisBand.SUPPRESSED)
        result = apply_evidence_governance(inp, readiness, macro, flag_enabled=True)
        assert result.governed_evidence_quality == AxisBand.STRONG.value


# ═══════════════════════════════════════════════════════════════════════════════
# J. NO PROVIDER/LLM/DB CALLS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoIoCalls:

    def test_governance_module_no_io_imports(self):
        import app.services.intelligence.v3.intel_v3_evidence_aware_governance_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        forbidden = ["import requests", "import httpx", "import aiohttp", "asyncpg", "supabase"]
        for f_str in forbidden:
            assert f_str not in content

    def test_source_credibility_module_no_io_imports(self):
        import app.services.intelligence.v3.source_credibility_registry_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        forbidden = ["import requests", "import httpx", "import aiohttp", "asyncpg", "supabase"]
        for f_str in forbidden:
            assert f_str not in content

    def test_governance_does_not_call_decide(self):
        import app.services.intelligence.v3.intel_v3_evidence_aware_governance_v1 as mod
        assert not hasattr(mod, "decide")


# ═══════════════════════════════════════════════════════════════════════════════
# K. NO RAW PAYLOAD/SECRET LEAKS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoSecretLeaks:

    def test_governance_result_no_raw_payload(self):
        readiness = _make_readiness("SAFE", fund=READINESS_LIMITED)
        inp = _make_inp("SAFE")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        result_str = str(result.to_dict())
        forbidden = ["api_key", "source_url", "http://", "https://", "payload", "secret"]
        for f in forbidden:
            assert f not in result_str.lower(), f"Found '{f}' in governance result"

    def test_override_per_source_no_source_url(self):
        src = _make_yfinance_history_source()
        result = assess_artifact_sources([src])
        for per_src in result.per_source_assessments:
            per_src_str = str(per_src)
            assert "source_url" not in per_src_str.lower()
            assert "api_key" not in per_src_str.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# L. SENTIMENT INTENTIONALLY STAYS SUPPRESSED
# ═══════════════════════════════════════════════════════════════════════════════


class TestSentimentHonestSuppression:
    """Sentiment (editorial news) stays SUPPRESSED_INCOMPLETE — no change."""

    def test_editorial_authority_still_in_thin_cap_levels(self):
        from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
            _THIN_CAP_AUTHORITY_LEVELS,
        )
        assert AuthorityLevel.EDITORIAL_CONTEXT.value in _THIN_CAP_AUTHORITY_LEVELS

    def test_sentiment_suppressed_does_not_block_buy_alone(self):
        """Sentiment suppressed alone: fundamentals usable → BUY not blocked."""
        readiness = _make_readiness("X", fund=READINESS_LIMITED, sent=READINESS_SUPPRESSED)
        inp = _make_inp("X", evidence_quality=AxisBand.SUPPRESSED)
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        decision = decide(inp)
        # fund=LIMITED + sent=SUPPRESSED(not usable) → Priority 4b → OK → BUY allowed
        assert result.governed_evidence_quality == AxisBand.OK.value
        assert decision.action == ActionV3.BUY

    def test_sentiment_suppressed_not_in_corroboration(self):
        """Suppressed sentiment does not count as corroboration."""
        readiness = _make_readiness("X", fund=READINESS_LIMITED, sent=READINESS_SUPPRESSED)
        band, codes, blocks, priority = _derive_governed_evidence_quality(readiness)
        assert priority == "p4b_limited_no_corroboration"  # not p4a


# ═══════════════════════════════════════════════════════════════════════════════
# M. NEW DIAGNOSTIC FIELDS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNewDiagnosticFields:

    def test_primary_evidence_readiness_in_result(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.primary_evidence_readiness == READINESS_LIMITED

    def test_auxiliary_evidence_readiness_populated(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED, tech=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        aux = result.auxiliary_evidence_readiness
        assert "technical_signals" in aux
        assert "sentiment" in aux
        assert aux["technical_signals"] == READINESS_LIMITED

    def test_corroboration_gap_true_when_fund_usable_no_tech_sent(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.corroboration_gap is True

    def test_corroboration_gap_false_when_tech_available(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED, tech=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.corroboration_gap is False

    def test_governance_priority_applied_string_set(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.governance_priority_applied == "p4b_limited_no_corroboration"

    def test_safe_for_visible_decision_reason_set(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert result.safe_for_visible_decision_reason != ""
        assert "primary_evidence_usable" in result.safe_for_visible_decision_reason

    def test_safe_for_visible_decision_reason_set_when_blocked(self):
        readiness = _make_readiness("X", fund=READINESS_SUPPRESSED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        assert "blocked" in result.safe_for_visible_decision_reason

    def test_to_dict_includes_all_new_fields(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=True)
        d = result.to_dict()
        new_keys = [
            "primary_evidence_readiness",
            "auxiliary_evidence_readiness",
            "corroboration_gap",
            "governance_priority_applied",
            "safe_for_visible_decision_reason",
        ]
        for k in new_keys:
            assert k in d, f"Missing new field '{k}' in to_dict()"

    def test_flag_off_new_fields_have_defaults(self):
        readiness = _make_readiness("X", fund=READINESS_LIMITED)
        inp = _make_inp("X")
        result = apply_evidence_governance(inp, readiness, None, flag_enabled=False)
        d = result.to_dict()
        assert "primary_evidence_readiness" in d
        assert "corroboration_gap" in d
        assert "governance_priority_applied" in d
        assert d["governance_priority_applied"] == "governance_inactive"


# ═══════════════════════════════════════════════════════════════════════════════
# N. PRIORITY STRINGS CORRECT
# ═══════════════════════════════════════════════════════════════════════════════


class TestPriorityStrings:

    def test_p1_suppressed(self):
        r = _make_readiness("X", fund=READINESS_SUPPRESSED)
        _, _, _, p = _derive_governed_evidence_quality(r)
        assert p == "p1_suppressed_fundamentals"

    def test_p2_all_missing(self):
        r = _make_readiness("X")
        _, _, _, p = _derive_governed_evidence_quality(r)
        assert p == "p2_all_missing_or_degraded"

    def test_p3a_ready_corroborated(self):
        r = _make_readiness("X", fund=READINESS_READY, tech=READINESS_READY)
        _, _, _, p = _derive_governed_evidence_quality(r)
        assert p == "p3a_ready_corroborated"

    def test_p3b_ready_no_corroboration(self):
        r = _make_readiness("X", fund=READINESS_READY)
        _, _, _, p = _derive_governed_evidence_quality(r)
        assert p == "p3b_ready_no_corroboration"

    def test_p4a_limited_corroborated(self):
        r = _make_readiness("X", fund=READINESS_LIMITED, tech=READINESS_READY)
        _, _, _, p = _derive_governed_evidence_quality(r)
        assert p == "p4a_limited_corroborated"

    def test_p4b_limited_no_corroboration(self):
        r = _make_readiness("X", fund=READINESS_LIMITED)
        _, _, _, p = _derive_governed_evidence_quality(r)
        assert p == "p4b_limited_no_corroboration"

    def test_p5_no_fundamental_anchor(self):
        r = _make_readiness("X", tech=READINESS_READY)
        _, _, _, p = _derive_governed_evidence_quality(r)
        assert p == "p5_no_fundamental_anchor"
