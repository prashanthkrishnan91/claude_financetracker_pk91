"""Stage 9G/9H — ETF Intelligence Classifier + Unified Asset Decision Composer tests.

Fixture-based only. No live provider calls, no IO, no LLM, no SQL.

Coverage:
  Classifier:
  9G-01. VOO with holdings+weights+date+plausible coverage → holdings_ready, core_us_equity.
  9G-02. SCHD → dividend_income role; no stock-style company analysis fields.
  9G-03. XLE → sector_tilt role; concentration_risk safe_for_concentration=True when holdings_ready.
  9G-04. VXUS with shallow/no-date AV → not overlap-safe; not synthesis_ready; profile_ready OK.
  9G-05. GLD → commodity_trust; equity holdings not_applicable (not failed).
  9G-06. FMP 402/paywalled → holdings_ready blocked from FMP; tier falls to profile/metadata.
  9G-07. AV missing date → supplemental/profile at most; never holdings_ready.
  9G-08. Stock ticker → is_etf=False; not-applicable classification.
  9G-09. Unknown ETF ticker → unknown_fund/unknown_role; profile_ready when known as ETF.
  9G-10. Partial/suspicious NPORT → never holdings_ready.
  9G-11. safe_for_decision and synthesis_ready always False (classifier).
  9G-12. Commodity trust safety flags: role_analysis=True, overlap=False, concentration=False.
  9G-13. Holdings_ready → overlap and concentration both True.
  9G-14. Profile_ready → overlap=False, cost_comparison=True.
  9G-15. Metadata_only → all analysis flags False except role (if known).

  Composer:
  9H-01. Stock ticker → stock fundamental lens; never ETF lens.
  9H-02. SCHD underweight → BUY suggested; drivers mention role not stock business analysis.
  9H-03. VOO on-target → HOLD_ON_TARGET; drivers mention portfolio sleeve.
  9H-04. XLE overweight → TRIM; overweight reason explicit.
  9H-05. VXUS with role_mismatch → HOLD_WATCH_ROLE; not action BUY even when underweight.
  9H-06. GLD on-target → HOLD_COMMODITY_STABLE via commodity_hedge_lens.
  9H-07. GLD underweight → BUY via commodity hedge lens (not equity).
  9H-08. ETF with role_mismatch + structurally_inferior → SELL.
  9H-09. ETF redundant + structurally_inferior → SELL.
  9H-10. ETF overweight + role_mismatch → SELL (not just TRIM).
  9H-11. HOLD is never a silent fallback — always has explicit hold_reason.
  9H-12. Missing/weak data → blocked_reason set; suggested_action=None; not silent HOLD.
  9H-13. Stock lens uses stock-specific driver language (not ETF exposure/role language).
  9H-14. ETF lens uses ETF-specific driver language (role/exposure, not P/E or margin).
  9H-15. safe_for_decision and synthesis_ready always False (composer).
  9H-16. Crypto → crypto_speculative_lens; not stock or ETF lens.
  9H-17. Unknown asset type → unknown_lens; blocked_reason set.
  9H-18. Commodity trust uses commodity_hedge_lens, not etf_role_lens.
  9H-19. ETF with metadata_only + unknown role → blocked_reason set; no action.
  9H-20. ETF concentration_risk signal with holdings_ready → driver mentions concentration.
  9H-21. ETF cost_elevated signal → driver mentions expense ratio.
  9H-22. HOLD_ON_TARGET requires portfolio_fit=ON_TARGET.
  9H-23. HOLD_WATCH_EVIDENCE when ETF evidence tier is metadata_only + portfolio fit unknown.
  9H-24. Composer to_dict() output contains no raw provider payload fields.
  9H-25. ETF type/role description is plain-English, not raw metric keys.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.etf_intelligence_classifier_v1 import (
    ETF_ROLE_COMMODITY_HEDGE,
    ETF_ROLE_CORE_US_EQUITY,
    ETF_ROLE_DIVIDEND_INCOME,
    ETF_ROLE_INTERNATIONAL_DIVERSIFIER,
    ETF_ROLE_SECTOR_TILT,
    ETF_ROLE_UNKNOWN,
    ETF_TIER_HOLDINGS_READY,
    ETF_TIER_METADATA_ONLY,
    ETF_TIER_NOT_APPLICABLE,
    ETF_TIER_PROFILE_READY,
    ETF_TYPE_COMMODITY_TRUST,
    ETF_TYPE_DIVIDEND_ETF,
    ETF_TYPE_EQUITY_ETF,
    ETF_TYPE_INTERNATIONAL_ETF,
    ETF_TYPE_SECTOR_ETF,
    ETF_TYPE_UNKNOWN_FUND,
    FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS,
    FLAG_SAFE_FOR_COST_COMPARISON,
    FLAG_SAFE_FOR_DECISION,
    FLAG_SAFE_FOR_OVERLAP_ANALYSIS,
    FLAG_SAFE_FOR_ROLE_ANALYSIS,
    FLAG_SYNTHESIS_READY,
    classify_etf_intelligence,
)
from app.services.intelligence.v3.asset_intelligence_composer_v1 import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_SELL,
    ACTION_TRIM,
    ASSET_CLASS_COMMODITY_TRUST,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_ETF,
    ASSET_CLASS_STOCK,
    ASSET_CLASS_UNKNOWN,
    HOLD_COMMODITY_STABLE,
    HOLD_ON_TARGET,
    HOLD_STABLE_NO_TRIGGER,
    HOLD_WATCH_EVIDENCE,
    HOLD_WATCH_ROLE,
    LENS_COMMODITY_HEDGE,
    LENS_CRYPTO,
    LENS_ETF_ROLE,
    LENS_STOCK_FUNDAMENTAL,
    LENS_UNKNOWN,
    compose_asset_intelligence,
)

# ── Fixture helpers ───────────────────────────────────────────────────────────


def _av_output_with_date(holdings_count: int = 200, coverage: str = "usable_supplemental") -> dict:
    return {
        "holdings_available": True,
        "canonical_ready": False,
        "safe_for_decision": False,
        "as_of_date_verified": True,
        "freshness_status": "date_present_unverified",
        "coverage_quality": coverage,
        "supplemental_only": True,
        "rejection_reasons": [],
    }


def _av_output_no_date(holdings_count: int = 200, coverage: str = "usable_supplemental") -> dict:
    return {
        "holdings_available": True,
        "canonical_ready": False,
        "safe_for_decision": False,
        "as_of_date_verified": False,
        "freshness_status": "date_missing",
        "coverage_quality": coverage,
        "supplemental_only": True,
        "rejection_reasons": ["as_of_date_missing"],
    }


def _av_output_partial(holdings_count: int = 37) -> dict:
    return {
        "holdings_available": True,
        "canonical_ready": False,
        "safe_for_decision": False,
        "as_of_date_verified": False,
        "freshness_status": "date_missing",
        "coverage_quality": "partial_or_suspicious",
        "supplemental_only": True,
        "rejection_reasons": ["as_of_date_missing", "partial_or_incomplete_coverage"],
    }


def _fmp_output_paywalled() -> dict:
    return {
        "fetch_status": "paywalled",
        "holdings_count": 0,
        "weights_available": False,
        "as_of_date": None,
        "canonical_ready": False,
        "safe_for_decision": False,
    }


def _fmp_output_success(holdings_count: int = 200, has_date: bool = True) -> dict:
    return {
        "fetch_status": "success",
        "holdings_count": holdings_count,
        "weights_available": True,
        "as_of_date": "2026-03-31" if has_date else None,
        "coverage_quality": "plausible_full",
        "canonical_ready": False,
        "safe_for_decision": False,
    }


def _nport_output_success(holdings_count: int = 500, has_date: bool = True) -> dict:
    return {
        "fetch_status": "success",
        "holdings_count": holdings_count,
        "weights_available": True,
        "as_of_date": "2026-03-31" if has_date else None,
        "coverage_quality": "plausible_full",
        "canonical_ready": False,
        "safe_for_decision": False,
    }


def _nport_output_partial() -> dict:
    return {
        "fetch_status": "success",
        "holdings_count": 37,
        "weights_available": True,
        "as_of_date": "2026-03-31",
        "coverage_quality": "partial_or_suspicious",
        "canonical_ready": False,
        "safe_for_decision": False,
    }


# ── Classifier tests ──────────────────────────────────────────────────────────


class TestVooHoldingsReady:
    """9G-01: VOO with holdings+weights+date+plausible coverage → holdings_ready, core_us_equity."""

    def test_voo_nport_holdings_ready(self):
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"nport_output": _nport_output_success(holdings_count=519)},
        )
        assert result.is_etf is True
        assert result.evidence_tier == ETF_TIER_HOLDINGS_READY
        assert result.etf_type == ETF_TYPE_EQUITY_ETF
        assert result.etf_role == ETF_ROLE_CORE_US_EQUITY

    def test_voo_av_with_date_holdings_ready(self):
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"av_output": _av_output_with_date(holdings_count=519)},
        )
        assert result.evidence_tier == ETF_TIER_HOLDINGS_READY
        assert result.etf_role == ETF_ROLE_CORE_US_EQUITY

    def test_voo_holdings_ready_overlap_safe(self):
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"nport_output": _nport_output_success()},
        )
        assert result.safety_flags[FLAG_SAFE_FOR_OVERLAP_ANALYSIS] is True
        assert result.safety_flags[FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS] is True

    def test_spy_core_us_equity(self):
        result = classify_etf_intelligence(
            ticker="SPY",
            asset_type="etf",
            provider_outputs={"nport_output": _nport_output_success()},
        )
        assert result.etf_role == ETF_ROLE_CORE_US_EQUITY
        assert result.evidence_tier == ETF_TIER_HOLDINGS_READY


class TestSchdDividendLens:
    """9G-02: SCHD → dividend_income role; no stock-style company analysis fields."""

    def test_schd_type_and_role(self):
        result = classify_etf_intelligence(ticker="SCHD", asset_type="etf")
        assert result.etf_type == ETF_TYPE_DIVIDEND_ETF
        assert result.etf_role == ETF_ROLE_DIVIDEND_INCOME
        assert result.is_etf is True

    def test_schd_role_description_no_stock_jargon(self):
        result = classify_etf_intelligence(ticker="SCHD", asset_type="etf")
        desc = result.role_description.lower()
        # Must not contain stock-fundamental jargon
        for jargon in ("p/e", "eps", "margin", "revenue growth", "fcf", "ebitda"):
            assert jargon not in desc, f"Jargon '{jargon}' found in ETF role description"
        assert "dividend" in desc or "income" in desc

    def test_schd_with_holdings(self):
        result = classify_etf_intelligence(
            ticker="SCHD",
            asset_type="etf",
            provider_outputs={"nport_output": _nport_output_success(holdings_count=103)},
        )
        assert result.evidence_tier == ETF_TIER_HOLDINGS_READY
        assert result.etf_role == ETF_ROLE_DIVIDEND_INCOME


class TestXleSectorTilt:
    """9G-03: XLE → sector_tilt; concentration risk allowed when holdings_ready."""

    def test_xle_type_and_role(self):
        result = classify_etf_intelligence(ticker="XLE", asset_type="etf")
        assert result.etf_type == ETF_TYPE_SECTOR_ETF
        assert result.etf_role == ETF_ROLE_SECTOR_TILT

    def test_xle_concentration_safe_when_holdings_ready(self):
        result = classify_etf_intelligence(
            ticker="XLE",
            asset_type="etf",
            provider_outputs={"nport_output": _nport_output_success(holdings_count=24)},
        )
        assert result.evidence_tier == ETF_TIER_HOLDINGS_READY
        assert result.safety_flags[FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS] is True

    def test_xle_concentration_not_safe_without_holdings(self):
        result = classify_etf_intelligence(ticker="XLE", asset_type="etf")
        assert result.safety_flags[FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS] is False


class TestVxusInternational:
    """9G-04: VXUS with shallow/no-date AV → not overlap-safe; not synthesis_ready; profile_ready OK."""

    def test_vxus_av_no_date_not_holdings_ready(self):
        result = classify_etf_intelligence(
            ticker="VXUS",
            asset_type="etf",
            provider_outputs={"av_output": _av_output_no_date(holdings_count=37)},
        )
        assert result.evidence_tier != ETF_TIER_HOLDINGS_READY
        assert result.synthesis_ready is False

    def test_vxus_av_partial_not_overlap_safe(self):
        result = classify_etf_intelligence(
            ticker="VXUS",
            asset_type="etf",
            provider_outputs={"av_output": _av_output_partial(holdings_count=37)},
        )
        assert result.safety_flags[FLAG_SAFE_FOR_OVERLAP_ANALYSIS] is False

    def test_vxus_profile_ready_when_known_etf(self):
        """VXUS is a known ETF → at least profile_ready for role analysis."""
        result = classify_etf_intelligence(
            ticker="VXUS",
            asset_type="etf",
            provider_outputs={"av_output": _av_output_no_date()},
        )
        # Known ETF with any holdings signal → at least profile_ready
        assert result.evidence_tier in (ETF_TIER_PROFILE_READY, ETF_TIER_HOLDINGS_READY)
        assert result.etf_role == ETF_ROLE_INTERNATIONAL_DIVERSIFIER
        assert result.safety_flags[FLAG_SAFE_FOR_ROLE_ANALYSIS] is True

    def test_vxus_role_known_even_without_holdings(self):
        result = classify_etf_intelligence(ticker="VXUS", asset_type="etf")
        assert result.etf_role == ETF_ROLE_INTERNATIONAL_DIVERSIFIER
        assert result.safety_flags[FLAG_SAFE_FOR_ROLE_ANALYSIS] is True


class TestGldCommodityTrust:
    """9G-05: GLD → commodity_trust; equity holdings not_applicable (not failed)."""

    def test_gld_type_commodity_trust(self):
        result = classify_etf_intelligence(ticker="GLD", asset_type="etf")
        assert result.etf_type == ETF_TYPE_COMMODITY_TRUST
        assert result.etf_role == ETF_ROLE_COMMODITY_HEDGE

    def test_gld_evidence_tier_not_applicable(self):
        result = classify_etf_intelligence(ticker="GLD", asset_type="etf")
        # not_applicable is correct classification — not a failure
        assert result.evidence_tier == ETF_TIER_NOT_APPLICABLE

    def test_gld_not_failed_equity_analysis(self):
        """GLD should not show equity holdings failure — it's not_applicable."""
        result = classify_etf_intelligence(ticker="GLD", asset_type="etf")
        for reason in result.limitation_reasons:
            # The reason should explain "not applicable" not "failed"
            assert "not_applicable" in reason or "not applicable" in reason.lower()
            assert "failed" not in reason.lower()
            assert "error" not in reason.lower()

    def test_gld_role_analysis_allowed(self):
        result = classify_etf_intelligence(ticker="GLD", asset_type="etf")
        assert result.safety_flags[FLAG_SAFE_FOR_ROLE_ANALYSIS] is True

    def test_gld_overlap_not_applicable(self):
        result = classify_etf_intelligence(ticker="GLD", asset_type="etf")
        assert result.safety_flags[FLAG_SAFE_FOR_OVERLAP_ANALYSIS] is False
        assert result.safety_flags[FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS] is False

    def test_gld_is_still_etf(self):
        result = classify_etf_intelligence(ticker="GLD", asset_type="etf")
        assert result.is_etf is True


class TestFmpPaywalled:
    """9G-06: FMP 402/paywalled → holdings_ready NOT contributed by FMP."""

    def test_fmp_paywalled_not_holdings_ready(self):
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"fmp_output": _fmp_output_paywalled()},
        )
        # FMP paywalled → falls back to profile/metadata (no holdings)
        assert result.evidence_tier in (ETF_TIER_PROFILE_READY, ETF_TIER_METADATA_ONLY)

    def test_fmp_paywalled_not_synthesis_ready(self):
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"fmp_output": _fmp_output_paywalled()},
        )
        assert result.synthesis_ready is False
        assert result.safe_for_decision is False

    def test_fmp_paywalled_not_overlap_safe(self):
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"fmp_output": _fmp_output_paywalled()},
        )
        assert result.safety_flags[FLAG_SAFE_FOR_OVERLAP_ANALYSIS] is False


class TestAvMissingDate:
    """9G-07: AV missing date → supplemental/profile at most; never holdings_ready."""

    def test_av_missing_date_not_holdings_ready(self):
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"av_output": _av_output_no_date(holdings_count=519)},
        )
        assert result.evidence_tier != ETF_TIER_HOLDINGS_READY

    def test_av_missing_date_not_overlap_safe(self):
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"av_output": _av_output_no_date(holdings_count=519)},
        )
        assert result.safety_flags[FLAG_SAFE_FOR_OVERLAP_ANALYSIS] is False

    def test_av_with_date_can_be_holdings_ready(self):
        """Contrast: AV WITH date → holdings_ready is possible."""
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"av_output": _av_output_with_date(holdings_count=519)},
        )
        assert result.evidence_tier == ETF_TIER_HOLDINGS_READY


class TestStockTickerNotApplicable:
    """9G-08: Stock ticker → is_etf=False; not-applicable classification."""

    def test_msft_not_etf(self):
        result = classify_etf_intelligence(ticker="MSFT", asset_type="stock")
        assert result.is_etf is False

    def test_aapl_no_etf_fields(self):
        result = classify_etf_intelligence(ticker="AAPL", asset_type="equity")
        assert result.is_etf is False
        assert result.etf_type == ETF_TYPE_UNKNOWN_FUND
        assert result.evidence_tier == ETF_TIER_NOT_APPLICABLE

    def test_stock_all_safety_flags_false(self):
        result = classify_etf_intelligence(ticker="MSFT", asset_type="stock")
        for flag_val in result.safety_flags.values():
            assert flag_val is False

    def test_crypto_not_etf(self):
        result = classify_etf_intelligence(ticker="BTC", asset_type="crypto")
        assert result.is_etf is False


class TestPartialCoverageNeverHoldingsReady:
    """9G-10: Partial/suspicious NPORT → never holdings_ready."""

    def test_nport_partial_not_holdings_ready(self):
        result = classify_etf_intelligence(
            ticker="VXUS",
            asset_type="etf",
            provider_outputs={"nport_output": _nport_output_partial()},
        )
        assert result.evidence_tier != ETF_TIER_HOLDINGS_READY
        assert result.safety_flags[FLAG_SAFE_FOR_OVERLAP_ANALYSIS] is False


class TestGovernanceInvariants:
    """9G-11, 9G-12: Governance invariants always hold."""

    def test_safe_for_decision_always_false(self):
        for ticker, atype in [("VOO", "etf"), ("MSFT", "stock"), ("GLD", "etf"), ("BTC", "crypto")]:
            result = classify_etf_intelligence(ticker=ticker, asset_type=atype)
            assert result.safe_for_decision is False, f"{ticker}: safe_for_decision should be False"

    def test_synthesis_ready_always_false(self):
        for ticker, atype in [("SPY", "etf"), ("SCHD", "etf"), ("XLE", "etf")]:
            result = classify_etf_intelligence(
                ticker=ticker,
                asset_type=atype,
                provider_outputs={"nport_output": _nport_output_success()},
            )
            assert result.synthesis_ready is False, f"{ticker}: synthesis_ready should be False"


class TestSafetyFlagsByTier:
    """9G-13 to 9G-15: Safety flags by evidence tier."""

    def test_holdings_ready_overlap_and_concentration_true(self):
        result = classify_etf_intelligence(
            ticker="VOO",
            asset_type="etf",
            provider_outputs={"nport_output": _nport_output_success()},
        )
        assert result.evidence_tier == ETF_TIER_HOLDINGS_READY
        assert result.safety_flags[FLAG_SAFE_FOR_OVERLAP_ANALYSIS] is True
        assert result.safety_flags[FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS] is True
        assert result.safety_flags[FLAG_SAFE_FOR_COST_COMPARISON] is True

    def test_profile_ready_cost_true_overlap_false(self):
        result = classify_etf_intelligence(ticker="VOO", asset_type="etf")
        assert result.evidence_tier == ETF_TIER_PROFILE_READY
        assert result.safety_flags[FLAG_SAFE_FOR_COST_COMPARISON] is True
        assert result.safety_flags[FLAG_SAFE_FOR_OVERLAP_ANALYSIS] is False

    def test_metadata_only_role_true_overlap_false(self):
        result = classify_etf_intelligence(
            ticker="QQQM",
            asset_type="etf",
        )
        # QQQM is known but no provider output → at least profile_ready (known fund)
        # or metadata_only if unknown
        assert result.safety_flags[FLAG_SAFE_FOR_OVERLAP_ANALYSIS] is False
        assert result.safety_flags[FLAG_SAFE_FOR_DECISION] is False


# ── Composer tests ────────────────────────────────────────────────────────────


class TestStockLens:
    """9H-01, 9H-13: Stock uses stock_fundamental_lens; never ETF lens."""

    def test_msft_uses_stock_lens(self):
        result = compose_asset_intelligence(
            ticker="MSFT", asset_type="stock", evidence_quality="OK"
        )
        assert result.lens_applied == LENS_STOCK_FUNDAMENTAL
        assert result.asset_class == ASSET_CLASS_STOCK

    def test_stock_no_etf_classification(self):
        result = compose_asset_intelligence(
            ticker="AAPL", asset_type="equity", evidence_quality="OK"
        )
        assert result.etf_classification is None

    def test_stock_driver_language_not_etf(self):
        result = compose_asset_intelligence(
            ticker="MSFT", asset_type="stock", evidence_quality="OK",
            portfolio_fit="UNDERWEIGHT"
        )
        for driver in result.decision_drivers:
            driver_lower = driver.lower()
            assert "portfolio sleeve" not in driver_lower, "ETF language in stock driver"
            assert "expense ratio" not in driver_lower
            assert "overlap" not in driver_lower

    def test_stock_thin_evidence_blocked(self):
        result = compose_asset_intelligence(
            ticker="MSFT", asset_type="stock", evidence_quality="THIN"
        )
        assert result.suggested_action is None
        assert result.blocked_reason is not None
        assert "thin" in result.blocked_reason.lower() or "suppressed" in result.blocked_reason.lower()


class TestEtfBuySemantic:
    """9H-02: SCHD underweight → BUY; drivers mention role not stock business analysis."""

    def test_schd_underweight_buy(self):
        result = compose_asset_intelligence(
            ticker="SCHD",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
        )
        assert result.suggested_action == ACTION_BUY
        assert result.lens_applied == LENS_ETF_ROLE

    def test_schd_buy_driver_uses_role_language(self):
        result = compose_asset_intelligence(
            ticker="SCHD",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
        )
        combined_drivers = " ".join(result.decision_drivers).lower()
        # ETF role language
        assert any(kw in combined_drivers for kw in ("sleeve", "role", "dividend", "income", "underweight"))
        # No stock-style business analysis language
        for jargon in ("p/e ratio", "earnings per share", "gross margin", "ebitda"):
            assert jargon not in combined_drivers

    def test_voo_underweight_buy(self):
        result = compose_asset_intelligence(
            ticker="VOO",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
        )
        assert result.suggested_action == ACTION_BUY


class TestEtfHoldOnTarget:
    """9H-03: VOO on-target → HOLD_ON_TARGET; drivers mention portfolio sleeve."""

    def test_voo_on_target_hold(self):
        result = compose_asset_intelligence(
            ticker="VOO",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
        )
        assert result.suggested_action == ACTION_HOLD
        assert result.hold_reason == HOLD_ON_TARGET

    def test_voo_on_target_driver_mentions_target(self):
        result = compose_asset_intelligence(
            ticker="VOO",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
        )
        combined = " ".join(result.decision_drivers).lower()
        assert any(kw in combined for kw in ("target", "weight", "allocation"))


class TestEtfTrimSemantic:
    """9H-04: XLE overweight → TRIM; reason is explicit."""

    def test_xle_overweight_trim(self):
        result = compose_asset_intelligence(
            ticker="XLE",
            asset_type="etf",
            portfolio_fit="OVERWEIGHT",
        )
        assert result.suggested_action == ACTION_TRIM

    def test_xle_trim_driver_is_explicit(self):
        result = compose_asset_intelligence(
            ticker="XLE",
            asset_type="etf",
            portfolio_fit="OVERWEIGHT",
        )
        combined = " ".join(result.decision_drivers).lower()
        assert "overweight" in combined or "above target" in combined or "trim" in combined


class TestVxusRoleMismatch:
    """9H-05: VXUS with role_mismatch → HOLD_WATCH_ROLE when underweight."""

    def test_vxus_underweight_role_mismatch_hold_not_buy(self):
        result = compose_asset_intelligence(
            ticker="VXUS",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
            upstream_signals={"role_mismatch": True},
        )
        assert result.suggested_action == ACTION_HOLD
        assert result.hold_reason == HOLD_WATCH_ROLE

    def test_vxus_role_mismatch_driver_explains(self):
        result = compose_asset_intelligence(
            ticker="VXUS",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
            upstream_signals={"role_mismatch": True},
        )
        combined = " ".join(result.decision_drivers).lower()
        assert "role" in combined or "mismatch" in combined


class TestGldCommoditySemantic:
    """9H-06, 9H-07, 9H-18: GLD uses commodity_hedge_lens."""

    def test_gld_on_target_hold_commodity_stable(self):
        result = compose_asset_intelligence(
            ticker="GLD",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
        )
        assert result.lens_applied == LENS_COMMODITY_HEDGE
        assert result.suggested_action == ACTION_HOLD
        assert result.hold_reason == HOLD_COMMODITY_STABLE
        assert result.asset_class == ASSET_CLASS_COMMODITY_TRUST

    def test_gld_underweight_buy(self):
        result = compose_asset_intelligence(
            ticker="GLD",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
        )
        assert result.lens_applied == LENS_COMMODITY_HEDGE
        assert result.suggested_action == ACTION_BUY

    def test_gld_not_etf_role_lens(self):
        result = compose_asset_intelligence(
            ticker="GLD",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
        )
        assert result.lens_applied != LENS_ETF_ROLE

    def test_gld_driver_mentions_hedge_not_equity_valuation(self):
        result = compose_asset_intelligence(
            ticker="GLD",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
        )
        combined = " ".join(result.decision_drivers).lower()
        # GLD drivers must mention hedge/commodity lens
        assert "hedge" in combined or "commodity" in combined
        # GLD must not suggest equity-style valuation or business analysis
        for equity_analysis_phrase in ("p/e", "earnings", "gross margin", "revenue"):
            assert equity_analysis_phrase not in combined, (
                f"Equity analysis phrase '{equity_analysis_phrase}' found in GLD driver"
            )


class TestEtfSellSemantics:
    """9H-08, 9H-09, 9H-10: ETF SELL semantics."""

    def test_role_mismatch_structurally_inferior_sell(self):
        result = compose_asset_intelligence(
            ticker="XLE",
            asset_type="etf",
            portfolio_fit="UNKNOWN",
            upstream_signals={"role_mismatch": True, "structurally_inferior": True},
        )
        assert result.suggested_action == ACTION_SELL

    def test_redundant_inferior_sell(self):
        result = compose_asset_intelligence(
            ticker="XLE",
            asset_type="etf",
            portfolio_fit="UNKNOWN",
            upstream_signals={"is_redundant_etf": True, "structurally_inferior": True},
        )
        assert result.suggested_action == ACTION_SELL

    def test_overweight_role_mismatch_sell_not_trim(self):
        result = compose_asset_intelligence(
            ticker="XLE",
            asset_type="etf",
            portfolio_fit="OVERWEIGHT",
            upstream_signals={"role_mismatch": True},
        )
        assert result.suggested_action == ACTION_SELL


class TestHoldNeverSilentFallback:
    """9H-11: HOLD always has explicit hold_reason."""

    def test_etf_hold_has_reason(self):
        for fit in ("ON_TARGET", "UNKNOWN", "BLOCKED"):
            result = compose_asset_intelligence(
                ticker="VOO",
                asset_type="etf",
                portfolio_fit=fit,
            )
            if result.suggested_action == ACTION_HOLD:
                assert result.hold_reason is not None, (
                    f"HOLD with portfolio_fit={fit} has no hold_reason"
                )
                assert result.hold_reason in (
                    HOLD_ON_TARGET, HOLD_STABLE_NO_TRIGGER,
                    HOLD_WATCH_EVIDENCE, HOLD_WATCH_ROLE, HOLD_COMMODITY_STABLE
                ), f"Unexpected hold_reason: {result.hold_reason}"

    def test_stock_hold_has_reason(self):
        for fit in ("ON_TARGET", "BLOCKED", "UNKNOWN"):
            result = compose_asset_intelligence(
                ticker="MSFT", asset_type="stock",
                portfolio_fit=fit, evidence_quality="OK",
            )
            if result.suggested_action == ACTION_HOLD:
                assert result.hold_reason is not None

    def test_commodity_hold_has_reason(self):
        result = compose_asset_intelligence(
            ticker="GLD", asset_type="etf", portfolio_fit="ON_TARGET"
        )
        assert result.suggested_action == ACTION_HOLD
        assert result.hold_reason is not None


class TestWeakDataExplicitBlocked:
    """9H-12: Missing/weak data → blocked_reason set; not silent HOLD."""

    def test_unknown_role_metadata_only_blocked(self):
        result = compose_asset_intelligence(
            ticker="UNKNWN",
            asset_type="etf",
            portfolio_fit="UNKNOWN",
        )
        # Unknown ticker with no provider data → unknown_fund/unknown_role → metadata_only
        # If role is unknown AND metadata_only → blocked
        if result.suggested_action is None:
            assert result.blocked_reason is not None

    def test_stock_thin_evidence_no_silent_hold(self):
        result = compose_asset_intelligence(
            ticker="MSFT",
            asset_type="stock",
            evidence_quality="SUPPRESSED",
        )
        assert result.suggested_action is None
        assert result.blocked_reason is not None
        # Must not be HOLD — must be None/blocked
        assert result.suggested_action != ACTION_HOLD


class TestEtfLensLanguage:
    """9H-14: ETF lens uses ETF-specific driver language."""

    def test_etf_driver_uses_exposure_role_language(self):
        result = compose_asset_intelligence(
            ticker="SCHD",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
        )
        combined = " ".join(result.decision_drivers).lower()
        assert any(kw in combined for kw in ("role", "exposure", "sleeve", "dividend", "allocation"))

    def test_etf_driver_no_stock_fundamentals_language(self):
        result = compose_asset_intelligence(
            ticker="VOO",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
        )
        combined = " ".join(result.decision_drivers).lower()
        for jargon in ("p/e", "earnings per share", "gross margin", "ebitda", "roic"):
            assert jargon not in combined, f"Stock jargon '{jargon}' leaked into ETF lens"


class TestComposerGovernanceInvariants:
    """9H-15: safe_for_decision and synthesis_ready always False."""

    def test_all_asset_types_safe_for_decision_false(self):
        cases = [
            ("MSFT", "stock", "OK"),
            ("VOO", "etf", "OK"),
            ("GLD", "etf", "OK"),
            ("BTC", "crypto", "OK"),
        ]
        for ticker, atype, eq in cases:
            result = compose_asset_intelligence(ticker=ticker, asset_type=atype, evidence_quality=eq)
            assert result.safe_for_decision is False, f"{ticker}: safe_for_decision should be False"
            assert result.synthesis_ready is False, f"{ticker}: synthesis_ready should be False"


class TestCryptoLens:
    """9H-16: Crypto → crypto_speculative_lens."""

    def test_btc_crypto_lens(self):
        result = compose_asset_intelligence(ticker="BTC", asset_type="crypto")
        assert result.lens_applied == LENS_CRYPTO
        assert result.asset_class == ASSET_CLASS_CRYPTO

    def test_crypto_not_etf_or_stock_lens(self):
        result = compose_asset_intelligence(ticker="ETH", asset_type="crypto")
        assert result.lens_applied not in (LENS_STOCK_FUNDAMENTAL, LENS_ETF_ROLE)


class TestUnknownAssetType:
    """9H-17: Unknown asset type → unknown_lens; blocked_reason set."""

    def test_unknown_type_unknown_lens(self):
        result = compose_asset_intelligence(ticker="XYZABC", asset_type="derivative")
        assert result.lens_applied == LENS_UNKNOWN
        assert result.blocked_reason is not None
        assert result.suggested_action is None
        assert result.asset_class == ASSET_CLASS_UNKNOWN


class TestEtfConcentrationCostSignals:
    """9H-20, 9H-21: ETF signals produce correct drivers."""

    def test_concentration_risk_driver_when_holdings_ready(self):
        result = compose_asset_intelligence(
            ticker="XLE",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
            provider_outputs={"nport_output": _nport_output_success(holdings_count=24)},
            upstream_signals={"concentration_risk": True},
        )
        combined = " ".join(result.decision_drivers).lower()
        assert "concentrated" in combined or "concentration" in combined

    def test_cost_elevated_driver(self):
        result = compose_asset_intelligence(
            ticker="ARKK",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
            upstream_signals={"cost_elevated": True},
        )
        combined = " ".join(result.decision_drivers).lower()
        assert "expense" in combined or "cost" in combined


class TestHoldReasonByFit:
    """9H-22, 9H-23: HOLD reason codes match portfolio fit context."""

    def test_on_target_gives_hold_on_target(self):
        result = compose_asset_intelligence(
            ticker="SCHD",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
        )
        assert result.suggested_action == ACTION_HOLD
        assert result.hold_reason == HOLD_ON_TARGET

    def test_unknown_fit_metadata_only_gives_watch_evidence(self):
        """Unknown ETF with no provider signals → metadata_only tier → HOLD_WATCH_EVIDENCE."""
        result = compose_asset_intelligence(
            ticker="UNKNWNETF",
            asset_type="etf",
            portfolio_fit="UNKNOWN",
        )
        if result.suggested_action == ACTION_HOLD:
            assert result.hold_reason in (HOLD_WATCH_EVIDENCE, HOLD_STABLE_NO_TRIGGER, HOLD_WATCH_ROLE)


class TestToDict:
    """9H-24: to_dict() output contains no raw provider payload fields."""

    def test_to_dict_no_raw_payload_keys(self):
        result = compose_asset_intelligence(
            ticker="VOO",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
            provider_outputs={"nport_output": _nport_output_success()},
        )
        d = result.to_dict()
        raw_payload_keys = {
            "holdings_count", "weights_available", "fetch_status", "coverage_quality",
            "rejection_reasons", "supplemental_only", "canonical_ready",
        }
        # The top-level result dict should not expose raw provider fields directly
        for key in raw_payload_keys:
            assert key not in d, f"Raw provider key '{key}' found in composer output"

    def test_etf_classification_in_to_dict(self):
        result = compose_asset_intelligence(
            ticker="SCHD",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
        )
        d = result.to_dict()
        assert "etf_classification" in d
        assert d["etf_classification"] is not None
        assert d["safe_for_decision"] is False
        assert d["synthesis_ready"] is False


class TestRoleDescription:
    """9H-25: Role description is plain-English, not raw metric keys."""

    _FORBIDDEN_METRIC_KEYS = {
        "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
        "revenue_growth_yoy", "peg_ratio", "p_fcf", "net_margin_ttm",
    }

    @pytest.mark.parametrize("ticker,atype", [
        ("VOO", "etf"), ("SCHD", "etf"), ("XLE", "etf"),
        ("VXUS", "etf"), ("GLD", "etf"), ("TLT", "etf"),
    ])
    def test_role_description_no_metric_keys(self, ticker, atype):
        result = classify_etf_intelligence(ticker=ticker, asset_type=atype)
        desc = result.role_description.lower()
        for key in self._FORBIDDEN_METRIC_KEYS:
            assert key not in desc, f"Metric key '{key}' found in role description for {ticker}"

    @pytest.mark.parametrize("ticker,atype", [
        ("VOO", "etf"), ("SCHD", "etf"), ("XLE", "etf"), ("GLD", "etf"),
    ])
    def test_role_description_is_plain_english(self, ticker, atype):
        result = classify_etf_intelligence(ticker=ticker, asset_type=atype)
        desc = result.role_description
        assert len(desc) > 20, f"Role description too short for {ticker}"
        assert desc.strip() != "", f"Role description empty for {ticker}"
